"""Supplemental source reporting; the original comparison threshold is unchanged."""

import hashlib
import io
from itertools import combinations
from zipfile import ZipFile

import numpy as np

from physics_guided_forecast_error.artifacts import (
    ROOT,
    check_hashes,
    check_index,
    read_json,
    sha,
)
from physics_guided_history_availability.core import (
    COLUMNS,
    read_csv_prefix,
    read_xlsx_prefix,
)
from physics_guided_history_availability.workflow import (
    build_outputs,
    source_guard as original_guard,
    specification as original_specification,
)

CONFIG = ROOT / "config/ootang_bplus_history_completion.v1_16_1.json"
CONFIG_SHA = "af24653f4301f9830aca2d04a17d9b02973202db63c3da7c1f4acc0e7459e97e"
PLAN_SHA = "e1a311d98b794d5b970c0152c4b89fd430239042f90b6af587076fb0c87fb084"
__all__ = ["build_outputs"]


def specification():
    extra = read_json(CONFIG)
    if (
        sha(CONFIG) != CONFIG_SHA
        or sha(ROOT / extra["protocol"]) != PLAN_SHA
        or sha(ROOT / extra["original_config"]) != extra["original_config_sha256"]
    ):
        raise ValueError("Registered supplemental design changed")
    return {**original_specification(), **extra}


def source_guard(spec):
    protected = original_guard(original_specification())
    failed = ROOT / spec["failed_run"]
    if sha(failed / "artifact_manifest.json") != spec["failed_index_sha256"]:
        raise ValueError("Original failed audit index changed")
    check_index(failed)
    if (
        read_json(failed / "launcher.json")["exitcode"] != 1
        or not (failed / "failed.json").exists()
    ):
        raise ValueError("Original failure must remain a failure")
    protected.update(read_json(failed / "manifest.json")["sources"])
    protected.update(
        {str(p.relative_to(ROOT)): sha(p) for p in failed.rglob("*") if p.is_file()}
    )
    check_hashes(protected)
    return protected


def compare_prefixes(sources, atol):
    if set(sources) != {"csv", "xlsx", "mentor"}:
        raise ValueError("All three source prefixes are required")
    comparisons = {}
    for a, b in combinations(("csv", "xlsx", "mentor"), 2):
        if not np.array_equal(sources[a]["dates"], sources[b]["dates"]):
            raise ValueError("Source prefix calendars differ")
        va, vb = sources[a]["values"], sources[b]["values"]
        if va.shape != vb.shape or va.ndim != 2 or va.shape[1] != len(COLUMNS):
            raise ValueError("Source prefix shapes differ")
        if not np.isfinite(va).all() or not np.isfinite(vb).all():
            raise ValueError("Nonfinite source prefix values")
        delta = np.abs(va - vb)
        i, j = np.unravel_index(np.argmax(delta), delta.shape)
        comparisons[a + "_vs_" + b] = dict(
            compared_values=int(delta.size),
            max_abs_difference=float(np.max(delta)),
            max_abs_by_column=dict(zip(COLUMNS, np.max(delta, axis=0).tolist())),
            above_original_tolerance=int(np.sum(delta > atol)),
            above_original_tolerance_by_column=dict(
                zip(COLUMNS, np.sum(delta > atol, axis=0).tolist())
            ),
            passed=bool(np.max(delta) <= atol),
            maximum_example=dict(
                source_index=int(i),
                date=str(sources[a]["dates"][i]),
                column=COLUMNS[j],
                value_a=float(va[i, j]),
                value_b=float(vb[i, j]),
            ),
        )
    if not comparisons["csv_vs_xlsx"]["passed"]:
        raise ValueError("Published CSV/XLSX must satisfy the original tolerance")
    return comparisons


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
    comparisons = compare_prefixes(sources, spec["source_numeric_atol"])
    if sources["csv"]["source_columns"] != sources["xlsx"]["source_columns"]:
        raise ValueError("CSV/XLSX header order differs")
    lineage = read_json(ROOT / spec["lineage_manifest"])
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
        original_three_source_equivalence_passed=all(
            v["passed"] for v in comparisons.values()
        ),
        published_source_equivalence_passed=comparisons["csv_vs_xlsx"]["passed"],
        upstream_lineage_status=lineage["lineage_status"],
        source_recovery_status=lineage["source_recovery_status"],
        raw_observation_as_of_verified="unknown",
    )
