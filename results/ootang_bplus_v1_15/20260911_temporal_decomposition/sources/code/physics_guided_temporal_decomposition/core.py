"""Signed linear groups and descriptive marginal training ranges."""

import numpy as np
import pandas as pd

from physics_guided_temporal_features.core import POINTS

GROUPS = {
    "hydro": (0, 12),
    "history": (12, 24),
    "s": (24, 28),
    "p": (28, 32),
    "rb": (32, 36),
    "rc": (36, 40),
    "rE": (40, 44),
    "background": (44, 48),
}


def slices(dimensions):
    if dimensions not in (12, 24, 48):
        raise ValueError("Unregistered feature dimension")
    return {k: slice(a, b) for k, (a, b) in GROUPS.items() if b <= dimensions}


def bounds(z, origin, start):
    z = np.asarray(z, dtype=float)
    if z.ndim != 2 or not 0 <= start < origin < len(z) or not np.isfinite(z).all():
        raise ValueError(
            "Finite inputs with exact nonempty fit and forward rows required"
        )
    return z[start:origin].min(axis=0), z[start:origin].max(axis=0)


def decompose(z, coefficient, origin, start=30, scale=100):
    z, coefficient = np.asarray(z), np.asarray(coefficient)
    lo, hi = bounds(z, origin, start)
    if coefficient.shape != (z.shape[1] + 1, 4) or not np.isfinite(coefficient).all():
        raise ValueError("Exact finite four-point coefficients required")
    groups = slices(z.shape[1])
    terms = scale * z[:, :, None] * coefficient[None, 1:, :]
    beyond = scale * (z - np.clip(z, lo, hi))[:, :, None] * coefficient[None, 1:, :]
    intercept = np.broadcast_to(scale * coefficient[0], (len(z), 4))
    parts = np.stack(
        [intercept, *[terms[:, ix].sum(axis=1) for ix in groups.values()]], axis=1
    )
    excess = np.stack(
        [
            np.zeros_like(intercept),
            *[beyond[:, ix].sum(axis=1) for ix in groups.values()],
        ],
        axis=1,
    )
    return dict(groups=np.array(["intercept", *groups]), parts=parts, excess=excess)


def range_arrays(z, origin, start):
    lo, hi = bounds(z, origin, start)
    forward = z[origin:]
    below, above = forward < lo, forward > hi
    distance = np.maximum(np.maximum(lo - forward, forward - hi), 0)
    return dict(
        lo=lo, hi=hi, below=below, above=above, outside=below | above, distance=distance
    )


def build_tables(spec, daily, feature_names):
    rows = {name: [] for name in spec["table_rows"]}
    for origin in spec["origins"]:
        data = daily[origin]
        for name in spec["representations"]:
            for part, start, stop in (
                ("train", 30, origin),
                ("forward", origin, len(data["z"])),
            ):
                ix = slice(start, stop)
                total = data[name + "_parts"][ix].sum(axis=1)
                excess = data[name + "_excess"][ix].sum(axis=1)
                demand = data["demand"][ix]
                for j, point in enumerate(POINTS):
                    info = dict(
                        origin=origin,
                        representation=name,
                        part=part,
                        station=point,
                        days=stop - start,
                    )
                    d, c = demand[:, j], total[:, j]
                    error = c - d
                    active = (abs(d) > spec["direction_tolerance_mm"]) & (
                        abs(c) > spec["direction_tolerance_mm"]
                    )
                    opposed = active & (d * c < 0)
                    rows["total"].append(
                        dict(
                            **info,
                            demand_mean_mm=d.mean(),
                            correction_mean_mm=c.mean(),
                            boundary_mean_mm=(c - excess[:, j]).mean(),
                            excess_mean_mm=excess[:, j].mean(),
                            rmse_mm=np.sqrt(np.mean(error**2)),
                            mae_mm=np.mean(abs(error)),
                            bias_mm=error.mean(),
                            active_days=int(active.sum()),
                            opposed_days=int(opposed.sum()),
                            opposed_fraction=opposed.sum() / active.sum()
                            if active.any()
                            else np.nan,
                        )
                    )
                    for k, group in enumerate(data[name + "_groups"]):
                        values, beyond = (
                            data[name + "_parts"][ix, k, j],
                            data[name + "_excess"][ix, k, j],
                        )
                        rows["group"].append(
                            dict(
                                **info,
                                group=group,
                                mean_mm=values.mean(),
                                rms_mm=np.sqrt(np.mean(values**2)),
                                sd_mm=values.std(),
                                min_mm=values.min(),
                                max_mm=values.max(),
                                boundary_mean_mm=(values - beyond).mean(),
                                excess_mean_mm=beyond.mean(),
                            )
                        )
        for reference, start in (("scaler_prefix", 0), ("fit_rows", 30)):
            limits = range_arrays(data["z"], origin, start)
            future = data["z"][origin:]
            for j, feature in enumerate(feature_names):
                rows["ranges"].append(
                    dict(
                        origin=origin,
                        reference=reference,
                        feature=feature,
                        reference_start=start,
                        reference_end_exclusive=origin,
                        days=len(future),
                        reference_min_z=limits["lo"][j],
                        reference_max_z=limits["hi"][j],
                        forward_min_z=future[:, j].min(),
                        forward_max_z=future[:, j].max(),
                        forward_mean_z=future[:, j].mean(),
                        below_days=int(limits["below"][:, j].sum()),
                        above_days=int(limits["above"][:, j].sum()),
                        outside_fraction=limits["outside"][:, j].mean(),
                        max_excess_z=limits["distance"][:, j].max(),
                    )
                )
            collections = {
                **slices(48),
                **{"all_" + k: slice(0, n) for k, n in spec["representations"].items()},
            }
            for group, ix in collections.items():
                outside = limits["outside"][:, ix].any(axis=1)
                rows["range_groups"].append(
                    dict(
                        origin=origin,
                        reference=reference,
                        group=group,
                        days=len(future),
                        outside_days=int(outside.sum()),
                        outside_fraction=outside.mean(),
                    )
                )
    tables = {name: pd.DataFrame(values) for name, values in rows.items()}
    if {name: len(table) for name, table in tables.items()} != spec["table_rows"]:
        raise ValueError("Incomplete registered tables")
    return tables
