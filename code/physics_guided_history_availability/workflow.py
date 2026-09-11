"""Frozen source identity, month-boundary links, and audit output construction."""

import calendar
import hashlib
import io
from datetime import timedelta
from itertools import combinations
from zipfile import ZipFile

import numpy as np
import pandas as pd

from physics_guided_forecast_error.artifacts import ROOT, check_hashes, read_json, sha
from physics_guided_rate_diagnostics.workflow import source_guard as frozen_guard
from .core import (
    COLUMNS,
    POINTS,
    SOURCE_ROLE,
    START,
    UNKNOWN,
    materialized_history,
    read_csv_prefix,
    read_xlsx_prefix,
)

CONFIG = ROOT / "config/ootang_bplus_history_availability.v1_16.json"
CONFIG_SHA = "b7eee431815c53cc98964e14ac2ca22809202bf88428a572cce46547f7caba10"
PLAN_SHA = "95501230528e1e9ecb7036b1c208da988ec4c2e08f57208d11194033015913a9"


def specification():
    spec = read_json(CONFIG)
    if sha(CONFIG) != CONFIG_SHA or sha(ROOT / spec["protocol"]) != PLAN_SHA:
        raise ValueError("Registered history audit design changed")
    return spec


def source_guard(spec):
    protected = frozen_guard(spec)
    protected.update(spec["source_hashes"])
    name = "docs/ootang_bplus_temporal_decomposition_results.v1.15.md"
    protected[name] = sha(ROOT / name)
    lineage = read_json(ROOT / spec["lineage_manifest"])
    for item in lineage["artifacts"]:
        protected[item["path"]] = item["sha256"]
    check_hashes(protected)
    return protected


def source_audit(spec):
    days = spec["max_label_prefix"]
    sources = {
        "csv": read_csv_prefix(ROOT / spec["csv"], days),
        "xlsx": read_xlsx_prefix(ROOT / spec["xlsx"], days),
    }
    with ZipFile(ROOT / spec["zip"]) as archive:
        payload = archive.read(spec["zip_member"])
    member_sha = hashlib.sha256(payload).hexdigest()
    if member_sha != spec["zip_member_sha256"]:
        raise ValueError("Mentor monitoring member changed")
    with io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8-sig") as stream:
        sources["mentor"] = read_csv_prefix(stream, days, mentor=True)
    comparisons = {}
    for a, b in combinations(sources, 2):
        if not np.array_equal(sources[a]["dates"], sources[b]["dates"]):
            raise ValueError("Source prefix calendars differ")
        delta = np.abs(sources[a]["values"] - sources[b]["values"])
        if np.max(delta) > spec["source_numeric_atol"]:
            raise ValueError("Source prefix values differ beyond the frozen tolerance")
        comparisons[a + "_vs_" + b] = dict(
            compared_values=int(delta.size),
            max_abs_difference=float(np.max(delta)),
            max_abs_by_column=dict(zip(COLUMNS, np.max(delta, axis=0).tolist())),
        )
    if sources["csv"]["source_columns"] != sources["xlsx"]["source_columns"]:
        raise ValueError("CSV and XLSX header order differs")
    lineage = read_json(ROOT / spec["lineage_manifest"])
    status = lineage["lineage_status"]
    if (
        status["released_series_role"] != SOURCE_ROLE
        or status["future_information_usage"] != UNKNOWN
    ):
        raise ValueError("Frozen upstream availability semantics changed")
    return dict(
        source_hashes=spec["source_hashes"],
        zip_member_sha256=member_sha,
        source_numeric_atol=spec["source_numeric_atol"],
        numeric_prefix_rows=days,
        first_date=str(sources["csv"]["dates"][0]),
        last_date=str(sources["csv"]["dates"][-1]),
        numeric_column_order=list(COLUMNS),
        source_columns={k: s["source_columns"] for k, s in sources.items()},
        workbook=sources["xlsx"]["workbook"],
        comparisons=comparisons,
        upstream_lineage_status=status,
        source_recovery_status=lineage["source_recovery_status"],
        raw_observation_as_of_verified=UNKNOWN,
    )


def build_outputs(spec):
    fingerprints = pd.read_csv(ROOT / spec["fingerprints"])
    origins, links, history_rows, histories = [], [], [], {}
    for origin in spec["origins"]:
        history = materialized_history(ROOT / spec["csv"], origin, spec["history_days"])
        histories[origin] = history
        forecast = START + timedelta(days=origin)
        month = forecast.strftime("%Y-%m")
        n_month = calendar.monthrange(forecast.year, forecast.month)[1]
        origin_row = dict(
            origin=origin,
            first_forecast_date=forecast.isoformat(),
            last_available_date=str(history["dates"][-1]),
            history_start_date=str(history["dates"][0]),
            difference_left_date=(
                forecast - timedelta(days=spec["history_days"] + 1)
            ).isoformat(),
            month=month,
            month_days=n_month,
            prior_same_month_rows=forecast.day - 1,
            remaining_month_rows=n_month - forecast.day + 1,
            crosses_monthly_piece=1 < forecast.day <= n_month,
            raw_observation_as_of_verified=UNKNOWN,
        )
        origins.append(origin_row)
        for point in POINTS:
            selected = fingerprints[
                (fingerprints.month == month) & (fingerprints.column == point + "/mm")
            ]
            if len(selected) != 1 or int(selected.iloc[0].n_days) != n_month:
                raise ValueError(
                    "Missing, duplicate, or inconsistent frozen monthly fingerprint"
                )
            row = selected.iloc[0]
            links.append(
                dict(
                    origin=origin,
                    month=month,
                    station=point,
                    n_days=n_month,
                    prior_same_month_rows=forecast.day - 1,
                    remaining_month_rows=n_month - forecast.day + 1,
                    max_abs_d4=float(row.max_abs_d4),
                    max_abs_cubic_residual=float(row.max_abs_cubic_residual),
                    month_passes_cubic_fingerprint=bool(
                        row.month_passes_cubic_fingerprint
                    ),
                    raw_observation_as_of_verified=UNKNOWN,
                )
            )
        for t, day in enumerate(history["dates"]):
            for j, point in enumerate(POINTS):
                history_rows.append(
                    dict(
                        origin=origin,
                        source_index=origin - spec["history_days"] + t,
                        date=str(day),
                        station=point,
                        u_mm=float(history["u"][t, j]),
                        du_mm_per_day=float(history["du"][t, j]),
                    )
                )
    tables = {
        "origins": pd.DataFrame(origins),
        "monthly_links": pd.DataFrame(links),
        "history": pd.DataFrame(history_rows),
    }
    if {k: len(v) for k, v in tables.items()} != spec["table_rows"]:
        raise ValueError("Registered audit table row counts differ")
    availability = dict(
        source_role=SOURCE_ROLE,
        released_prefix_constructible=True,
        raw_observation_as_of_verified=UNKNOWN,
        future_upstream_anchor_usage=UNKNOWN,
        interpretation="Conditional historical prototype on a released daily modeling series",
        origins=[histories[c]["metadata"] for c in spec["origins"]],
    )
    return tables, histories, availability
