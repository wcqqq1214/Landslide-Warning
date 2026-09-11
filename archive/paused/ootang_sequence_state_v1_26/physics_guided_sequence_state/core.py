"""Same-weight state removal and fixed-bin descriptive diagnostics."""

import math

import numpy as np
import pandas as pd
import torch

from physics_guided_forecast_error.artifacts import sha
from physics_guided_sequence_learning.core import new_model, output
from physics_guided_sequence_learning.reference import predict as numpy_predict
from .support import arrays, require


def rms(values):
    return math.hypot(*np.asarray(values).reshape(-1).tolist()) / math.sqrt(np.size(values))


def close(actual, expected, tolerance):
    np.testing.assert_allclose(actual, expected, atol=tolerance, rtol=0)


def mean_forecast(inputs, correction):
    base, raw = inputs["base"], inputs["raw"]
    mean = np.full_like(base, np.nan)
    mean[29:31] = base[29:31]
    target = inputs["targets"]
    mean[target] = base[target] + correction[raw["row_batch"], raw["row_lead"]]
    require(np.isfinite(mean[29:]).all(), "Incomplete diagnostic mean")
    return mean


def probe_case(source, spec, h, sample, seed, inputs, budget):
    path = source / f"model_{h}_{sample}_CARRY_{seed}/e100.pt"
    model = new_model(seed)
    weights = torch.load(path, weights_only=True, map_location="cpu")
    template = model.state_dict()
    require(list(weights) == list(template)
            and sum(v.numel() for v in weights.values()) == spec["model_parameters"], "Changed checkpoint identity")
    for key, value in weights.items():
        require(value.shape == template[key].shape and value.dtype == torch.float64
                and torch.isfinite(value).all(), "Invalid frozen checkpoint")
    model.load_state_dict(weights)
    saved, max_reference = {}, 0.0
    for name, variant in (("correction", "CARRY"), ("s0_correction", "RESET")):
        with torch.no_grad():
            actual = output(model, inputs["encoder"], inputs["decoder"], variant, budget).numpy()
        reference = numpy_predict(model.state_dict(), inputs["raw"]["encoder"], inputs["raw"]["decoder"], variant, budget)
        close(actual, reference, spec["reference_atol_mm"])
        saved[name], saved["numpy_"+name] = actual, reference
        max_reference = max(max_reference, float(np.max(np.abs(actual-reference))))
    saved["mean"] = mean_forecast(inputs, saved["correction"])
    saved["s0_mean"] = mean_forecast(inputs, saved["s0_correction"])
    group = inputs["future_group"]
    saved["effect"] = saved["correction"][group] - saved["s0_correction"][group]
    independent_effect = saved["numpy_correction"][group] - saved["numpy_s0_correction"][group]
    close(saved["effect"], independent_effect, spec["difference_atol_mm"])
    original = arrays(source / f"mean_{h}_{sample}_CARRY.npz")["means"][seed]
    close(saved["mean"], original, spec["reference_atol_mm"])
    require(all(torch.equal(v, weights[k]) for k, v in model.state_dict().items()), "Frozen model changed")
    record = dict(fit_days=h, sample_source=sample, seed=seed,
                  checkpoint_sha256=sha(path), evaluation_sha256=sha(source / f"evaluation_{h}.npz"),
                  max_original_difference_mm=float(np.nanmax(np.abs(saved["mean"]-original))),
                  max_numpy_difference_mm=max_reference,
                  max_effect_reference_difference_mm=float(np.max(np.abs(saved["effect"]-independent_effect))))
    return saved, record


def components(spec, cases, inputs):
    for h in spec["prefixes"]:
        group = inputs[h]["future_group"]
        for sample in spec["sample_sources"]:
            selected = [cases[h, sample, seed] for seed in spec["seeds"]]
            combined = {key: np.mean(np.stack([s[key] for s in selected]), axis=0)
                        for key in ("correction", "s0_correction", "effect", "mean", "s0_mean")}
            for component, values in [*( (f"seed_{seed}", cases[h, sample, seed]) for seed in spec["seeds"]),
                                      ("ensemble", combined)]:
                yield (dict(fit_days=h, sample_source=sample, component=component),
                       dict(base=inputs[h]["base"], carry=values["correction"][group],
                            s0=values["s0_correction"][group], effect=values["effect"],
                            mean=values["mean"], s0_mean=values["s0_mean"]))


def effects(spec, values):
    rows = []
    for info, data in values:
        for j, station in enumerate(spec["point_order"]):
            for start, stop in spec["lead_bins"]:
                delta = data["effect"][start:stop, j]
                rows.append(dict(**info, station=station, start_lead=start, stop_lead=stop, days=stop-start,
                    effect_mean_mm=float(delta.mean()), effect_rms_mm=rms(delta),
                    effect_max_abs_mm=float(np.max(np.abs(delta))),
                    first_effect_mm=float(delta[0]), last_effect_mm=float(delta[-1])))
    return pd.DataFrame(rows)


def correction_statistics(correction, demand, tolerance):
    require(len(correction) == len(demand) > 0 and np.isfinite(correction).all()
            and np.isfinite(demand).all(), "Finite paired correction and demand required")
    product = correction*demand
    direction = np.sign(correction)*np.sign(demand)
    baseline_mse = np.mean(demand**2)
    mse = np.mean((correction-demand)**2)
    delta = float(mse-baseline_mse)
    identity = float(np.mean(correction**2-2*product))
    require(abs(delta-identity) <= tolerance, "Correction MSE identity failed")
    return dict(correction_mean_mm=float(correction.mean()), correction_rms_mm=rms(correction),
                rmse_mm=rms(correction-demand), mae_mm=float(np.mean(np.abs(correction-demand))),
                same_direction_days=int((direction > 0).sum()), opposite_direction_days=int((direction < 0).sum()),
                zero_product_days=int((direction == 0).sum()), mse_minus_P0_mm2=delta,
                mse_identity_residual_mm2=delta-identity)


def analysis_tables(spec, values, dates, labels):
    demand_rows, daily_rows, metric_rows = [], [], []
    for info, data in values:
        h = info["fit_days"]
        if h not in spec["scoring_prefixes"]:
            continue
        demand = labels[h:h+180] - data["base"][h:]
        for j, station in enumerate(spec["point_order"]):
            for lead in range(180):
                daily_rows.append(dict(**info, station=station, lead=lead, date=dates[h+lead],
                    observed_mm=float(labels[h+lead, j]), bplus_mm=float(data["base"][h+lead, j]),
                    demand_mm=float(demand[lead, j]), carry_correction_mm=float(data["carry"][lead, j]),
                    s0_correction_mm=float(data["s0"][lead, j]), state_effect_mm=float(data["effect"][lead, j])))
            for start, stop in spec["lead_bins"]:
                need = demand[start:stop, j]
                row = dict(**info, station=station, start_lead=start, stop_lead=stop, days=stop-start,
                           demand_mean_mm=float(need.mean()), demand_rms_mm=rms(need),
                           state_effect_rms_mm=rms(data["effect"][start:stop, j]))
                for condition in ("carry", "s0"):
                    stats = correction_statistics(data[condition][start:stop, j], need, spec["mse_identity_atol_mm2"])
                    row.update({condition+"_"+key: value for key, value in stats.items()})
                demand_rows.append(row)
            for part, start, stop in (("train", 30, h), ("prediction", h, h+180)):
                for condition, means in (("P0", data["base"]), ("CARRY", data["mean"]), ("S0", data["s0_mean"])):
                    error = means[start:stop, j]-labels[start:stop, j]
                    metric_rows.append(dict(**info, station=station, part=part, condition=condition,
                        days=stop-start, rmse_mm=rms(error), mae_mm=float(np.mean(np.abs(error)))))
    return dict(daily=pd.DataFrame(daily_rows), demand=pd.DataFrame(demand_rows), metrics=pd.DataFrame(metric_rows))


def comparisons(metrics, tolerance):
    rows = []
    indexed = metrics.set_index(["fit_days", "sample_source", "component", "station", "part", "condition"])
    require(indexed.index.is_unique, "Duplicate diagnostic metric keys")
    for key, _ in metrics.groupby(["fit_days", "sample_source", "component", "station"], sort=False):
        row = dict(zip(("fit_days", "sample_source", "component", "station"), key))
        for part in ("train", "prediction"):
            baseline = indexed.loc[(*key, part, "P0")]
            carry = indexed.loc[(*key, part, "CARRY")]
            s0 = indexed.loc[(*key, part, "S0")]
            for metric in ("rmse_mm", "mae_mm"):
                row[f"carry_{part}_{metric}_minus_P0"] = float(carry[metric]-baseline[metric])
                row[f"s0_{part}_{metric}_minus_P0"] = float(s0[metric]-baseline[metric])
                row[f"s0_{part}_{metric}_minus_carry"] = float(s0[metric]-carry[metric])
        for condition in ("carry", "s0"):
            row[condition+"_strict_diagnostic_improvement"] = all(
                row[f"{condition}_{part}_{metric}_minus_P0"] < -tolerance
                for part in ("train", "prediction") for metric in ("rmse_mm", "mae_mm"))
        rows.append(row)
    return pd.DataFrame(rows)
