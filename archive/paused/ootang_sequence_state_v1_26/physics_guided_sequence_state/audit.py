"""Scalar arithmetic, complete record identities and unchanged parent metrics."""

import math

import numpy as np

from .core import close
from .support import require, table

KEYS = ["fit_days", "sample_source", "component", "station"]


def scalar_rms(values):
    scale = max(abs(float(v)) for v in values)
    return scale*math.sqrt(math.fsum((float(v)/scale)**2 for v in values)/len(values)) if scale else 0.0


def scalar_mean(values):
    return math.fsum(float(v) for v in values)/len(values)


def identities(frame, columns, expected):
    require(not frame.duplicated(columns).any(), "Duplicate diagnostic identity")
    actual = set(frame[columns].itertuples(index=False, name=None))
    require(actual == expected, "Incomplete diagnostic identities")


def check_tables(spec, values, tables, dates, labels, source):
    for name, count in spec["table_rows"].items():
        require(len(tables[name]) == count, f"Wrong row count: {name}")
    bins = [tuple(b) for b in spec["lead_bins"]]
    contexts = {(v["fit_days"], v["sample_source"], v["component"], station): (v, d, j)
                for v, d in values for j, station in enumerate(spec["point_order"])}
    formal = {key: value for key, value in contexts.items() if key[0] in spec["scoring_prefixes"]}
    identities(tables["effects"], KEYS+["start_lead", "stop_lead"], {(*key, *b) for key in contexts for b in bins})
    identities(tables["demand"], KEYS+["start_lead", "stop_lead"], {(*key, *b) for key in formal for b in bins})
    identities(tables["daily"], KEYS+["lead"], {(*key, lead) for key in formal for lead in range(180)})
    identities(tables["metrics"], KEYS+["part", "condition"], {(*key, part, condition)
               for key in formal for part in ("train", "prediction") for condition in ("P0", "CARRY", "S0")})
    identities(tables["comparisons"], KEYS, set(formal))
    tol = spec["reference_atol_mm"]
    scalar_checks = 0
    for row in tables["effects"].itertuples(index=False):
        key = tuple(getattr(row, k) for k in KEYS)
        _, data, point = contexts[key]
        delta = data["effect"][row.start_lead:row.stop_lead, point]
        require(row.days == len(delta), "Effect segment length differs")
        close(row.effect_mean_mm, scalar_mean(delta), tol)
        np.testing.assert_allclose(row.effect_rms_mm, scalar_rms(delta), atol=0, rtol=1e-12)
        np.testing.assert_array_equal([row.effect_max_abs_mm, row.first_effect_mm, row.last_effect_mm],
                                      [max(abs(float(v)) for v in delta), delta[0], delta[-1]])
        scalar_checks += 5
    for row in tables["daily"].itertuples(index=False):
        key = tuple(getattr(row, k) for k in KEYS)
        _, data, point = formal[key]
        t = row.fit_days+row.lead
        require(row.date == dates[t], "Wrong observation date")
        np.testing.assert_array_equal(
            [row.observed_mm, row.bplus_mm, row.demand_mm, row.carry_correction_mm, row.s0_correction_mm, row.state_effect_mm],
            [labels[t, point], data["base"][t, point], labels[t, point]-data["base"][t, point],
             data["carry"][row.lead, point], data["s0"][row.lead, point], data["effect"][row.lead, point]])
        scalar_checks += 6
    max_identity_error = 0.0
    for row in tables["demand"].itertuples(index=False):
        key = tuple(getattr(row, k) for k in KEYS)
        _, data, point = formal[key]
        start, stop = row.start_lead, row.stop_lead
        need = [float(labels[row.fit_days+i, point])-float(data["base"][row.fit_days+i, point]) for i in range(start, stop)]
        require(row.days == len(need), "Demand segment length differs")
        close([row.demand_mean_mm, row.demand_rms_mm, row.state_effect_rms_mm],
              [scalar_mean(need), scalar_rms(need), scalar_rms(data["effect"][start:stop, point])], tol)
        for condition in ("carry", "s0"):
            correction = data[condition][start:stop, point].tolist()
            error = [c-d for c, d in zip(correction, need)]
            sign_counts = [0, 0, 0]
            for c, d in zip(correction, need):
                if c == 0 or d == 0:
                    sign_counts[2] += 1
                elif (c > 0) == (d > 0):
                    sign_counts[0] += 1
                else:
                    sign_counts[1] += 1
            names = ["correction_mean_mm", "correction_rms_mm", "rmse_mm", "mae_mm", "mse_minus_P0_mm2"]
            expected = [scalar_mean(correction), scalar_rms(correction), scalar_rms(error), scalar_mean([abs(v) for v in error]),
                        scalar_mean([c*c-2*c*d for c, d in zip(correction, need)])]
            close([getattr(row, condition+"_"+key) for key in names], expected, tol)
            np.testing.assert_array_equal([getattr(row, condition+"_"+key) for key in
                ("same_direction_days", "opposite_direction_days", "zero_product_days")], sign_counts)
            residual = abs(getattr(row, condition+"_mse_identity_residual_mm2"))
            require(residual <= spec["mse_identity_atol_mm2"], "Saved MSE identity failed")
            max_identity_error = max(max_identity_error, residual)
            scalar_checks += 9
        scalar_checks += 3
    original = table(source / "metrics.csv")
    seeds = table(source / "seed_metrics.csv")
    parent_checks = 0
    for row in tables["metrics"].itertuples(index=False):
        key = tuple(getattr(row, k) for k in KEYS)
        _, data, point = formal[key]
        start, stop = (30, row.fit_days) if row.part == "train" else (row.fit_days, row.fit_days+180)
        means = data[{"P0": "base", "CARRY": "mean", "S0": "s0_mean"}[row.condition]]
        errors = [float(means[t, point])-float(labels[t, point]) for t in range(start, stop)]
        require(row.days == len(errors), "Metric segment length differs")
        close([row.rmse_mm, row.mae_mm], [scalar_rms(errors), scalar_mean([abs(v) for v in errors])], tol)
        scalar_checks += 2
        if row.condition != "S0":
            ref = original if row.component == "ensemble" or row.condition == "P0" else seeds
            strategy = "P0" if row.condition == "P0" else row.sample_source+"_CARRY"
            chosen = ref[(ref.outer_days == row.fit_days) & (ref.strategy == strategy)
                         & (ref.station == row.station) & (ref.part == row.part)]
            if ref is seeds:
                chosen = chosen[chosen.component == row.component]
            require(len(chosen) == 1, "Original metric identity is incomplete")
            close([row.rmse_mm, row.mae_mm], chosen.iloc[0][["rmse_mm", "mae_mm"]].to_numpy(float), tol)
            parent_checks += 2
    indexed = tables["metrics"].set_index(KEYS+["part", "condition"])
    for row in tables["comparisons"].itertuples(index=False):
        key = tuple(getattr(row, k) for k in KEYS)
        for condition in ("carry", "s0"):
            differences = []
            for part in ("train", "prediction"):
                for metric in ("rmse_mm", "mae_mm"):
                    difference = indexed.loc[(*key, part, condition.upper()), metric]-indexed.loc[(*key, part, "P0"), metric]
                    close(getattr(row, f"{condition}_{part}_{metric}_minus_P0"), difference, tol)
                    differences.append(difference)
                    if condition == "s0":
                        close(getattr(row, f"s0_{part}_{metric}_minus_carry"),
                              indexed.loc[(*key, part, "S0"), metric]-indexed.loc[(*key, part, "CARRY"), metric], tol)
            require(getattr(row, condition+"_strict_diagnostic_improvement")
                    == all(v < -spec["strict_mean_tolerance_mm"] for v in differences), "Changed strict diagnostic criterion")
    return dict(scalar_values_checked=scalar_checks, original_metric_values_checked=parent_checks,
                max_mse_identity_residual_mm2=max_identity_error)
