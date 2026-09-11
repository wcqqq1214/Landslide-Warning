"""One preregistered input metric, deterministic neighbors, and diagnostic scores."""

import itertools

import numpy as np
import pandas as pd

POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")
GROUPS = {
    "displacement": (0, 2),
    "hydrology": (2, 16),
    "static": (16, 20),
    "history": (20, 22),
    "lead": (22, 23),
}
SEGMENTS = {
    "all": (0, 180),
    "days_1_30": (0, 30),
    "days_31_90": (30, 90),
    "days_91_180": (90, 180),
}
TOL = 1e-6


def squared_distances(x, y):
    if x.shape != y.shape or x.shape != (30, 23, 1, 4):
        raise ValueError("Distances require complete matching joint network inputs")
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("Nonfinite input")
    sq = (x - y) ** 2
    groups = np.array([sq[:, lo:hi].mean() for lo, hi in GROUPS.values()])
    return float(sq.mean()), groups


def match_inputs(h, origins, targets, candidate_x, query_x):
    origins, targets = np.asarray(origins), np.asarray(targets)
    if (
        origins.ndim != 1
        or targets.shape != origins.shape
        or origins.dtype.kind not in "iu"
        or targets.dtype.kind not in "iu"
        or candidate_x.shape != (len(origins), 30, 23, 1, 4)
        or query_x.ndim != 5
        or query_x.shape[1:] != (30, 23, 1, 4)
        or len(query_x) not in (90, 180)
        or np.any(origins >= h)
        or np.any(targets >= h)
        or np.any(origins < 31)
        or np.any(targets < origins)
        or np.any(targets - origins >= 180)
        or len(set(zip(origins.tolist(), targets.tolist()))) != len(origins)
    ):
        raise ValueError("Invalid strictly past same-lead candidate pool")
    edges, distances, groups, selected, references = [], [], [], [], []
    for lead, query in enumerate(query_x):
        candidates = np.flatnonzero(targets - origins == lead)
        if not len(candidates):
            raise ValueError("No observed candidate for this lead")
        options = []
        for k in candidates:
            d2, parts = squared_distances(query, candidate_x[k])
            edges.append((lead, k))
            distances.append(d2)
            groups.append(parts)
            options.append((d2, int(origins[k]), int(targets[k]), int(k)))
        selected.append(min(options)[3])
        prior = [
            np.sqrt(squared_distances(candidate_x[a], candidate_x[b])[0])
            for a, b in itertools.combinations(candidates, 2)
        ]
        references.append(
            [min(prior), np.median(prior), max(prior)] if prior else [np.nan] * 3
        )
    return dict(
        edges=np.array(edges, dtype=int),
        squared_distance=np.array(distances),
        group_mse=np.array(groups),
        chosen=np.array(selected, dtype=int),
        past_reference=np.array(references),
    )


def direction(a, b):
    nz = abs(a) > TOL and abs(b) > TOL
    return bool(nz), bool(nz and np.sign(a) == np.sign(b))


def build_tables(prefixes, labels):
    edge_rows, query_rows, metric_rows = [], [], []
    for (h, strategy), d in prefixes.items():
        n = len(d["query_base"])
        demand = labels[h : h + n] - d["query_base"]
        probe = d["candidate_correction"][d["chosen"]]
        norm = d["norm_mean"] - d["query_base"]
        selected_edges = {}
        for ei, (lead, k) in enumerate(d["edges"]):
            if d["chosen"][lead] == k:
                selected_edges[int(lead)] = ei
            for j, station in enumerate(POINTS):
                nz, same = direction(demand[lead, j], d["candidate_correction"][k, j])
                edge_rows.append(
                    dict(
                        fit_days=h,
                        strategy=strategy,
                        lead=int(lead),
                        target=int(h + lead),
                        station=station,
                        candidate_origin=int(d["origins"][k]),
                        candidate_target=int(d["targets"][k]),
                        distance=float(np.sqrt(d["squared_distance"][ei])),
                        **{
                            f"mse_{g}": float(d["group_mse"][ei, gi])
                            for gi, g in enumerate(GROUPS)
                        },
                        candidate_demand_mm=float(d["candidate_correction"][k, j]),
                        query_demand_mm=float(demand[lead, j]),
                        demand_difference_mm=float(
                            demand[lead, j] - d["candidate_correction"][k, j]
                        ),
                        nonzero_pair=nz,
                        same_direction=same,
                    )
                )
        for lead, k in enumerate(d["chosen"]):
            for j, station in enumerate(POINTS):
                nz, same = direction(demand[lead, j], probe[lead, j])
                query_rows.append(
                    dict(
                        fit_days=h,
                        strategy=strategy,
                        lead=lead,
                        target=h + lead,
                        station=station,
                        candidate_origin=int(d["origins"][k]),
                        candidate_target=int(d["targets"][k]),
                        distance=float(
                            np.sqrt(d["squared_distance"][selected_edges[lead]])
                        ),
                        past_distance_min=float(d["past_reference"][lead, 0]),
                        past_distance_median=float(d["past_reference"][lead, 1]),
                        past_distance_max=float(d["past_reference"][lead, 2]),
                        query_demand_mm=float(demand[lead, j]),
                        probe_correction_mm=float(probe[lead, j]),
                        norm_correction_mm=float(norm[lead, j]),
                        nonzero_pair=nz,
                        same_direction=same,
                    )
                )
        for segment, (start, stop) in SEGMENTS.items():
            stop = min(stop, n)
            if start >= stop:
                continue
            for j, station in enumerate(POINTS):
                truth = demand[start:stop, j]
                row = dict(
                    fit_days=h,
                    strategy=strategy,
                    segment=segment,
                    station=station,
                    days=stop - start,
                )
                for name, correction in [
                    ("P0", np.zeros(n)),
                    ("NORM", norm[:, j]),
                    ("PROBE", probe[:, j]),
                ]:
                    err = correction[start:stop] - truth
                    row[f"rmse_{name}_mm"] = float(np.sqrt(np.mean(err**2)))
                    row[f"mae_{name}_mm"] = float(np.mean(abs(err)))
                flags = [direction(a, b) for a, b in zip(truth, probe[start:stop, j])]
                row["nonzero_pairs"] = sum(nz for nz, _ in flags)
                row["same_direction_days"] = sum(same for _, same in flags)
                row["same_direction_fraction"] = (
                    row["same_direction_days"] / row["nonzero_pairs"]
                    if row["nonzero_pairs"]
                    else np.nan
                )
                for reference in ("P0", "NORM"):
                    row[f"forecast_improves_{reference}"] = all(
                        row[f"{m}_{reference}_mm"] - row[f"{m}_PROBE_mm"] > TOL
                        for m in ("rmse", "mae")
                    )
                metric_rows.append(row)
    return {
        "edges": pd.DataFrame(edge_rows),
        "queries": pd.DataFrame(query_rows),
        "metrics": pd.DataFrame(metric_rows),
    }
