"""Render the complete Ootang v3 station/site warning-state timeline.

The displayed states are post-observation audits: the target observation is
already available when its interval, kinematic, and fused states are assigned.
This module therefore makes no formal-warning or warning-lead-time claim.
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch

from warning import operational_v3_figure_support as figure_support
from warning.draft_evidence import FileReplacement, promote_staged_files

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = ROOT / "config" / "ootang_operational_run.v3.draft.json"
DEFAULT_FIGURE_SPEC_PATH = (
    ROOT / "config" / "ootang_operational_v3_typical_days.v1.json"
)
DEFAULT_OUTPUT_DIR = ROOT / "figures" / "warning_operational_draft_v3"
ARTIFACT_STATUS = figure_support.ARTIFACT_STATUS
LEVEL_ABBREVIATIONS = figure_support.LEVEL_ABBREVIATIONS
LEVEL_COLORS = figure_support.LEVEL_COLORS

FIGURE_STEM = "ootang_v3_full_warning_timeline"
MANIFEST_FILENAME = f"{FIGURE_STEM}_manifest.json"
NOT_CONFIRMED_CODE = 5
NOT_CONFIRMED_COLOR = "#C9C9C9"

FULL_TIMELINE_RC_PARAMS = {
    **figure_support.BASE_FIGURE_RC_PARAMS,
    "svg.hashsalt": "ootang-operational-v3-full-warning-timeline-v1",
    "font.size": 6.5,
}

STATION_REQUIRED_COLUMNS = {
    "date",
    "split",
    "station",
    "candidate_level",
    "formal_warning_output",
    "vajont_used",
}
SITE_REQUIRED_COLUMNS = {
    "date",
    "split",
    "site_fusion_status",
    "site_confirmed_level",
    "local_max_candidate_level",
    "formal_warning_output",
    "vajont_used",
}


class FullTimelineFigureInputError(ValueError):
    """Raised when a complete timeline cannot be traced to the v3 snapshot."""


@dataclass(frozen=True)
class FullTimelineFigureArtifacts:
    """Paths for one atomically promoted full-timeline figure bundle."""

    svg_path: Path
    pdf_path: Path
    png_path: Path
    manifest_path: Path


@dataclass(frozen=True)
class _TimelineData:
    station_rows: pd.DataFrame
    site_rows: pd.DataFrame
    dates: tuple[str, ...]
    splits: tuple[str, ...]
    stations: tuple[str, ...]
    station_blocks: dict[str, str]
    station_levels: np.ndarray
    site_levels: np.ndarray
    local_levels: np.ndarray
    site_not_confirmed_count: int


def _require_level_values(
    frame: pd.DataFrame,
    column: str,
    *,
    allow_not_confirmed: bool,
) -> pd.Series:
    numeric = pd.to_numeric(frame[column], errors="coerce")
    valid = numeric.between(0, 4) & np.isclose(numeric, np.round(numeric))
    if allow_not_confirmed:
        valid = valid | numeric.isna()
    if not bool(valid.all()):
        raise FullTimelineFigureInputError(
            f"Timeline contains invalid {column} values."
        )
    return numeric


def _require_nonformal_ootang_rows(frame: pd.DataFrame, *, label: str) -> None:
    if frame["formal_warning_output"].astype(bool).any():
        raise FullTimelineFigureInputError(f"{label} unexpectedly claims formal output.")
    if frame["vajont_used"].astype(bool).any():
        raise FullTimelineFigureInputError(f"{label} unexpectedly uses Vajont.")


def _prepare_timeline_data(
    *,
    station_rows: pd.DataFrame,
    site_rows: pd.DataFrame,
    profile: dict[str, Any],
    contract: dict[str, Any],
    core_manifest: dict[str, Any],
) -> _TimelineData:
    figure_support.require_columns(
        station_rows,
        STATION_REQUIRED_COLUMNS,
        label="station timeline",
        error_type=FullTimelineFigureInputError,
    )
    figure_support.require_columns(
        site_rows,
        SITE_REQUIRED_COLUMNS,
        label="site timeline",
        error_type=FullTimelineFigureInputError,
    )
    _require_nonformal_ootang_rows(station_rows, label="Station timeline")
    _require_nonformal_ootang_rows(site_rows, label="Site timeline")

    stations, station_blocks = figure_support.station_layout(
        profile,
        error_type=FullTimelineFigureInputError,
    )
    station_order = tuple(stations)

    ordered_site = site_rows.copy()
    ordered_site["date"] = ordered_site["date"].astype(str)
    ordered_site = ordered_site.sort_values("date", kind="stable").reset_index(
        drop=True
    )
    if ordered_site["date"].duplicated().any():
        raise FullTimelineFigureInputError(
            "Site timeline must contain exactly one row per date."
        )
    try:
        parsed_dates = pd.to_datetime(ordered_site["date"], errors="raise")
    except (TypeError, ValueError) as exc:
        raise FullTimelineFigureInputError(
            "Site timeline contains an invalid date."
        ) from exc
    if not parsed_dates.is_monotonic_increasing:
        raise FullTimelineFigureInputError("Site timeline dates are not chronological.")

    dates = tuple(ordered_site["date"])
    splits = tuple(ordered_site["split"].astype(str))
    if set(splits) != {"calibration", "test"}:
        raise FullTimelineFigureInputError(
            "Full timeline must contain calibration and test result splits."
        )
    split_transitions = sum(
        previous != current for previous, current in pairwise(splits)
    )
    if split_transitions != 1 or splits[0] != "calibration" or splits[-1] != "test":
        raise FullTimelineFigureInputError(
            "Full timeline must preserve one calibration-to-test transition."
        )

    ordered_station = station_rows.copy()
    ordered_station["date"] = ordered_station["date"].astype(str)
    ordered_station["station"] = ordered_station["station"].astype(str)
    expected_pairs = len(dates) * len(station_order)
    unique_pairs = ordered_station[["date", "station"]].drop_duplicates()
    if len(ordered_station) != expected_pairs or len(unique_pairs) != expected_pairs:
        raise FullTimelineFigureInputError(
            "Station timeline must contain exactly one row per station and date."
        )
    if set(ordered_station["date"]) != set(dates):
        raise FullTimelineFigureInputError(
            "Station and site timelines do not cover the same dates."
        )
    if set(ordered_station["station"]) != set(station_order):
        raise FullTimelineFigureInputError(
            "Station timeline does not contain the frozen eight-station set."
        )
    station_split = ordered_station.merge(
        ordered_site[["date", "split"]],
        on="date",
        how="left",
        validate="many_to_one",
        suffixes=("_station", "_site"),
    )
    if not station_split["split_station"].astype(str).eq(
        station_split["split_site"].astype(str)
    ).all():
        raise FullTimelineFigureInputError(
            "Station and site timeline split labels do not match."
        )

    station_numeric = _require_level_values(
        ordered_station,
        "candidate_level",
        allow_not_confirmed=False,
    )
    ordered_station["candidate_level"] = station_numeric.astype(int)
    local_numeric = _require_level_values(
        ordered_site,
        "local_max_candidate_level",
        allow_not_confirmed=False,
    )
    ordered_site["local_max_candidate_level"] = local_numeric.astype(int)
    site_numeric = _require_level_values(
        ordered_site,
        "site_confirmed_level",
        allow_not_confirmed=True,
    )
    not_confirmed = ordered_site["site_fusion_status"].eq(
        "candidate_not_site_confirmed"
    )
    valid_site = ordered_site["site_fusion_status"].eq("valid")
    if not (not_confirmed | valid_site).all():
        raise FullTimelineFigureInputError(
            "Site timeline contains an unsupported fusion status."
        )
    if not bool(site_numeric[not_confirmed].isna().all()) or not bool(
        site_numeric[valid_site].notna().all()
    ):
        raise FullTimelineFigureInputError(
            "Not-confirmed site states must be distinct from missing data."
        )
    site_not_confirmed_count = int(not_confirmed.sum())

    expected = {
        "date_count": len(dates),
        "station_count": len(station_order),
        "station_row_count": len(ordered_station),
        "site_not_confirmed_count": site_not_confirmed_count,
    }
    for name, actual in expected.items():
        configured = contract.get(f"expected_{name}")
        if isinstance(configured, bool) or not isinstance(configured, int):
            raise FullTimelineFigureInputError(
                f"Full-timeline contract must declare expected_{name}."
            )
        if actual != configured:
            raise FullTimelineFigureInputError(
                f"Full-timeline {name} drifted: expected {configured}, observed {actual}."
            )
    if contract.get("site_not_confirmed_status") != "candidate_not_site_confirmed":
        raise FullTimelineFigureInputError(
            "Full-timeline contract must preserve the v3 not-confirmed status."
        )
    if (
        contract.get("site_not_confirmed_semantics")
        != "candidate_not_site_confirmed_not_missing"
    ):
        raise FullTimelineFigureInputError(
            "Full-timeline contract must distinguish not-confirmed from missing."
        )

    result_counts = core_manifest.get("result_status_counts")
    declared_count = None
    if isinstance(result_counts, dict):
        site_counts = result_counts.get("site_fusion")
        if isinstance(site_counts, dict):
            declared_count = site_counts.get("candidate_not_site_confirmed")
    if declared_count != site_not_confirmed_count:
        raise FullTimelineFigureInputError(
            "Site not-confirmed count does not match the core run manifest."
        )

    indexed_station = ordered_station.set_index(["station", "date"])
    station_levels = np.asarray(
        [
            [indexed_station.loc[(station, date), "candidate_level"] for date in dates]
            for station in station_order
        ],
        dtype=float,
    )
    site_levels = site_numeric.to_numpy(dtype=float)
    local_levels = local_numeric.to_numpy(dtype=float)
    return _TimelineData(
        station_rows=ordered_station,
        site_rows=ordered_site,
        dates=dates,
        splits=splits,
        stations=station_order,
        station_blocks=station_blocks,
        station_levels=station_levels,
        site_levels=site_levels,
        local_levels=local_levels,
        site_not_confirmed_count=site_not_confirmed_count,
    )


def _date_ticks(dates: tuple[str, ...], *, count: int = 9) -> tuple[np.ndarray, list[str]]:
    indices = np.unique(
        np.rint(np.linspace(0, len(dates) - 1, min(count, len(dates)))).astype(int)
    )
    labels = [pd.Timestamp(dates[index]).strftime("%Y-%m") for index in indices]
    return indices, labels


def _draw_split_boundary(
    ax: plt.Axes,
    *,
    splits: tuple[str, ...],
    label_y: float,
) -> None:
    transition = next(
        index
        for index in range(1, len(splits))
        if splits[index] != splits[index - 1]
    )
    boundary = transition - 0.5
    ax.axvline(boundary, color="#202020", linewidth=0.8, linestyle="--")
    ax.text(
        boundary,
        label_y,
        "calibration | test",
        ha="center",
        va="bottom",
        fontsize=5.6,
        color="#202020",
        clip_on=False,
    )


def _style_timeline_axis(
    ax: plt.Axes,
    *,
    dates: tuple[str, ...],
    y_labels: list[str],
    show_x_labels: bool,
) -> None:
    ticks, labels = _date_ticks(dates)
    ax.set_yticks(np.arange(len(y_labels)))
    ax.set_yticklabels(y_labels, fontsize=6)
    ax.tick_params(axis="y", length=0, pad=4)
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels if show_x_labels else [], rotation=35, ha="right")
    ax.tick_params(axis="x", length=2, labelsize=5.8)
    for spine in ax.spines.values():
        spine.set_visible(False)


def _render_figure(data: _TimelineData) -> plt.Figure:
    level_names = tuple(LEVEL_COLORS)
    level_palette = [LEVEL_COLORS[name] for name in level_names]
    station_cmap = ListedColormap(level_palette)
    station_norm = BoundaryNorm(np.arange(-0.5, 5.5, 1), station_cmap.N)

    site_matrix = np.vstack(
        (
            np.where(
                np.isnan(data.site_levels),
                NOT_CONFIRMED_CODE,
                data.site_levels,
            ),
            data.local_levels,
        )
    )
    site_cmap = ListedColormap([*level_palette, NOT_CONFIRMED_COLOR])
    site_norm = BoundaryNorm(np.arange(-0.5, 6.5, 1), site_cmap.N)

    fig = plt.figure(figsize=(183 / 25.4, 118 / 25.4), facecolor="white")
    grid = fig.add_gridspec(
        2,
        1,
        height_ratios=(3.8, 1.2),
        left=0.115,
        right=0.985,
        bottom=0.19,
        top=0.77,
        hspace=0.38,
    )
    station_ax = fig.add_subplot(grid[0, 0])
    site_ax = fig.add_subplot(grid[1, 0])

    station_ax.imshow(
        data.station_levels,
        aspect="auto",
        cmap=station_cmap,
        norm=station_norm,
        interpolation="none",
        rasterized=True,
    )
    station_labels = [
        f"{station} ({data.station_blocks[station]})" for station in data.stations
    ]
    _style_timeline_axis(
        station_ax,
        dates=data.dates,
        y_labels=station_labels,
        show_x_labels=False,
    )
    _draw_split_boundary(station_ax, splits=data.splits, label_y=-0.65)
    station_ax.set_title(
        "a  Five-level candidate state for every monitoring station",
        loc="left",
        fontsize=7.2,
        fontweight="bold",
        pad=8,
    )

    site_ax.imshow(
        site_matrix,
        aspect="auto",
        cmap=site_cmap,
        norm=site_norm,
        interpolation="none",
        rasterized=True,
    )
    _style_timeline_axis(
        site_ax,
        dates=data.dates,
        y_labels=["site-confirmed", "local maximum"],
        show_x_labels=True,
    )
    _draw_split_boundary(site_ax, splits=data.splits, label_y=-0.65)
    site_ax.set_title(
        "b  Complete v3 dual-axis landslide-body timeline",
        loc="left",
        fontsize=7.2,
        fontweight="bold",
        pad=8,
    )

    fig.suptitle(
        f"Ootang v3 complete {len(data.dates)}-day warning-state timeline",
        x=0.07,
        y=0.965,
        ha="left",
        fontsize=9.2,
        fontweight="bold",
    )
    fig.text(
        0.07,
        0.915,
        "OPERATIONAL DRAFT • NOT FORMAL • OBSERVED-AFTER-FORECAST state audit",
        ha="left",
        va="center",
        fontsize=6.5,
        color="#8B2E2E",
        fontweight="bold",
    )
    fig.text(
        0.07,
        0.87,
        (
            f"{data.site_not_confirmed_count}/{len(data.dates)} site dates are "
            "NOT SITE-CONFIRMED (NOT MISSING); the local maximum remains visible."
        ),
        ha="left",
        va="center",
        fontsize=6.2,
        color="#303030",
    )
    fig.text(
        0.07,
        0.085,
        (
            "States use the target-date observation after its probabilistic forecast "
            "was issued; this panel does not demonstrate warning lead time or safety."
        ),
        ha="left",
        va="center",
        fontsize=5.8,
        color="#555555",
    )
    handles = [
        Patch(
            facecolor=LEVEL_COLORS[name],
            edgecolor="#303030",
            linewidth=0.4,
            label=f"{LEVEL_ABBREVIATIONS[name]} {name}",
        )
        for name in level_names
    ]
    handles.append(
        Patch(
            facecolor=NOT_CONFIRMED_COLOR,
            edgecolor="#303030",
            linewidth=0.4,
            label="NC not site-confirmed (not missing)",
        )
    )
    fig.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.52, 0.025),
        ncol=6,
        fontsize=5.6,
        handlelength=1.4,
        columnspacing=1.0,
    )
    return fig


def _count_levels(values: np.ndarray) -> dict[str, int]:
    return {
        name: int(np.sum(values == index))
        for index, name in enumerate(LEVEL_COLORS)
    }


def _plot_data_sha256(data: _TimelineData) -> str:
    return figure_support.canonical_json_sha256(
        {
            "dates": list(data.dates),
            "splits": list(data.splits),
            "stations": list(data.stations),
            "station_blocks": data.station_blocks,
            "station_candidate_levels": data.station_levels.astype(int).tolist(),
            "site_confirmed_levels": [
                None if np.isnan(value) else int(value) for value in data.site_levels
            ],
            "local_max_candidate_levels": data.local_levels.astype(int).tolist(),
            "site_fusion_status": data.site_rows["site_fusion_status"].tolist(),
        }
    )


def _figure_manifest(
    *,
    station_path: Path,
    site_path: Path,
    run_manifest_path: Path,
    profile_path: Path,
    figure_spec_path: Path,
    profile: dict[str, Any],
    figure_spec: dict[str, Any],
    contract: dict[str, Any],
    data: _TimelineData,
    figure_spec_sha256: str,
    run_manifest_sha256: str,
    station_sha256: str,
    site_sha256: str,
    renderer_sha256: str,
    shared_support_sha256: str,
    output_targets: tuple[Path, Path, Path],
    output_sources: tuple[Path, Path, Path],
) -> dict[str, Any]:
    output_records = {
        target.suffix.lstrip("."): {
            "path": figure_support.manifest_path(target),
            "sha256": figure_support.sha256_file(source),
            "n_bytes": source.stat().st_size,
        }
        for target, source in zip(output_targets, output_sources)
    }
    confirmed_values = data.site_levels[~np.isnan(data.site_levels)]
    return {
        "schema_version": "ootang_operational_v3_full_timeline_figure_v1",
        "artifact_kind": "ootang_operational_v3_complete_warning_state_timeline",
        "artifact_status": ARTIFACT_STATUS,
        "artifact_role": contract["artifact_role"],
        "formal_warning_output": False,
        "vajont_used": False,
        "case": "ootang",
        "observation_timing": {
            "status": "observed_after_forecast",
            "meaning": (
                "Each state is audited after the target-date observation is "
                "available; no warning lead time is claimed."
            ),
        },
        "operational_profile": {
            "id": profile["profile_id"],
            "version": profile["profile_version"],
            "path": figure_support.manifest_path(profile_path),
            "content_sha256": figure_support.canonical_json_sha256(profile),
        },
        "figure_spec": {
            "id": figure_spec["figure_id"],
            "version": figure_spec["figure_version"],
            "path": figure_support.manifest_path(figure_spec_path),
            "sha256": figure_spec_sha256,
            "full_timeline_contract": contract,
        },
        "implementation_sources": {
            "renderer": {
                "path": figure_support.manifest_path(Path(__file__).resolve()),
                "sha256": renderer_sha256,
            },
            "shared_figure_support": {
                "path": figure_support.manifest_path(
                    Path(figure_support.__file__).resolve()
                ),
                "sha256": shared_support_sha256,
            },
        },
        "source_inputs": {
            "core_run_manifest": {
                "path": figure_support.manifest_path(run_manifest_path),
                "sha256": run_manifest_sha256,
            },
            "station_timeline": {
                "path": figure_support.manifest_path(station_path),
                "sha256": station_sha256,
                "n_rows": len(data.station_rows),
                "core_manifest_match": True,
            },
            "site_timeline": {
                "path": figure_support.manifest_path(site_path),
                "sha256": site_sha256,
                "n_rows": len(data.site_rows),
                "core_manifest_match": True,
            },
        },
        "displayed_fields": {
            "station_axis": "candidate_level",
            "site_axis": "site_confirmed_level",
            "local_axis": "local_max_candidate_level",
        },
        "timeline_coverage": {
            "start_date": data.dates[0],
            "end_date": data.dates[-1],
            "date_count": len(data.dates),
            "station_count": len(data.stations),
            "station_row_count": len(data.station_rows),
            "site_row_count": len(data.site_rows),
            "site_not_confirmed_count": data.site_not_confirmed_count,
            "site_confirmed_count": len(data.dates)
            - data.site_not_confirmed_count,
            "site_missing_count": 0,
            "site_not_confirmed_semantics": (
                "candidate_not_site_confirmed_not_missing"
            ),
        },
        "warning_level_counts": {
            "station_candidate": _count_levels(data.station_levels),
            "site_confirmed": {
                **_count_levels(confirmed_values),
                "not_site_confirmed": data.site_not_confirmed_count,
            },
            "local_max_candidate": _count_levels(data.local_levels),
        },
        "plot_data_sha256": _plot_data_sha256(data),
        "rendering": {
            "backend": str(matplotlib.get_backend()).lower(),
            "matplotlib_version": matplotlib.__version__,
            "python_version": sys.version.split()[0],
            "width_mm": 183,
            "height_mm": 118,
            "png_dpi": 300,
            "svg_text_editable": True,
            "palette": {
                "levels": LEVEL_COLORS,
                "not_site_confirmed": NOT_CONFIRMED_COLOR,
            },
        },
        "outputs": output_records,
        "not_claimed": [
            "formal_warning_output",
            "warning_lead_time",
            "event_independent_warning_performance",
            "site_safety_on_not_confirmed_dates",
            "Vajont_external_case",
        ],
    }


def write_ootang_v3_full_timeline_figure(
    *,
    station_timeline_path: Path,
    site_timeline_path: Path,
    run_manifest_path: Path,
    profile_path: Path = DEFAULT_PROFILE_PATH,
    figure_spec_path: Path = DEFAULT_FIGURE_SPEC_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> FullTimelineFigureArtifacts:
    """Validate, render, and atomically promote the complete v3 timeline."""

    station_path = Path(station_timeline_path).resolve()
    site_path = Path(site_timeline_path).resolve()
    core_manifest_path = Path(run_manifest_path).resolve()
    operational_profile_path = Path(profile_path).resolve()
    spec_path = Path(figure_spec_path).resolve()
    target_dir = Path(output_dir).resolve()

    profile, _profile_payload = figure_support.read_json_snapshot(
        operational_profile_path,
        label="operational profile",
        error_type=FullTimelineFigureInputError,
    )
    figure_spec, figure_spec_payload = figure_support.read_json_snapshot(
        spec_path,
        label="figure specification",
        error_type=FullTimelineFigureInputError,
    )
    core_manifest, core_manifest_payload = figure_support.read_json_snapshot(
        core_manifest_path,
        label="core run manifest",
        error_type=FullTimelineFigureInputError,
    )
    station_rows, station_payload = figure_support.read_csv_snapshot(
        station_path,
        label="station timeline",
        error_type=FullTimelineFigureInputError,
    )
    site_rows, site_payload = figure_support.read_csv_snapshot(
        site_path,
        label="site timeline",
        error_type=FullTimelineFigureInputError,
    )
    renderer_payload = Path(__file__).resolve().read_bytes()
    shared_support_payload = Path(figure_support.__file__).resolve().read_bytes()

    if (
        figure_spec.get("formal_warning_output") is not False
        or figure_spec.get("vajont_used") is not False
        or figure_spec.get("case") != "ootang"
    ):
        raise FullTimelineFigureInputError(
            "Figure specification must remain non-formal and Ootang-only."
        )
    contract = figure_spec.get("full_timeline_contract")
    if not isinstance(contract, dict):
        raise FullTimelineFigureInputError(
            "Figure specification lacks a full-timeline contract."
        )

    figure_support.validate_run_provenance(
        manifest=core_manifest,
        station_sha256=figure_support.sha256_bytes(station_payload),
        site_sha256=figure_support.sha256_bytes(site_payload),
        profile=profile,
        error_type=FullTimelineFigureInputError,
    )
    data = _prepare_timeline_data(
        station_rows=station_rows,
        site_rows=site_rows,
        profile=profile,
        contract=contract,
        core_manifest=core_manifest,
    )

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    targets = (
        target_dir / f"{FIGURE_STEM}.svg",
        target_dir / f"{FIGURE_STEM}.pdf",
        target_dir / f"{FIGURE_STEM}.png",
    )
    manifest_target = target_dir / MANIFEST_FILENAME
    with tempfile.TemporaryDirectory(
        prefix=f".{target_dir.name}.full-timeline-",
        dir=target_dir.parent,
    ) as directory:
        staging_dir = Path(directory)
        with plt.rc_context(FULL_TIMELINE_RC_PARAMS):
            existing_figures = set(plt.get_fignums())
            try:
                figure = _render_figure(data)
            except BaseException:
                for figure_number in set(plt.get_fignums()).difference(
                    existing_figures
                ):
                    plt.close(figure_number)
                raise
            try:
                staged_outputs = figure_support.export_figure_bundle(
                    figure,
                    staging_dir,
                    figure_stem=FIGURE_STEM,
                    error_type=FullTimelineFigureInputError,
                )
            finally:
                plt.close(figure)

        staged_manifest = staging_dir / MANIFEST_FILENAME
        staged_manifest.write_text(
            json.dumps(
                _figure_manifest(
                    station_path=station_path,
                    site_path=site_path,
                    run_manifest_path=core_manifest_path,
                    profile_path=operational_profile_path,
                    figure_spec_path=spec_path,
                    profile=profile,
                    figure_spec=figure_spec,
                    contract=contract,
                    data=data,
                    figure_spec_sha256=figure_support.sha256_bytes(
                        figure_spec_payload
                    ),
                    run_manifest_sha256=figure_support.sha256_bytes(
                        core_manifest_payload
                    ),
                    station_sha256=figure_support.sha256_bytes(station_payload),
                    site_sha256=figure_support.sha256_bytes(site_payload),
                    renderer_sha256=figure_support.sha256_bytes(renderer_payload),
                    shared_support_sha256=figure_support.sha256_bytes(
                        shared_support_payload
                    ),
                    output_targets=targets,
                    output_sources=staged_outputs,
                ),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        promote_staged_files(
            tuple(
                FileReplacement(source, target)
                for source, target in zip(staged_outputs, targets)
            )
            + (FileReplacement(staged_manifest, manifest_target),)
        )

    return FullTimelineFigureArtifacts(
        svg_path=targets[0],
        pdf_path=targets[1],
        png_path=targets[2],
        manifest_path=manifest_target,
    )


__all__ = [
    "DEFAULT_FIGURE_SPEC_PATH",
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_PROFILE_PATH",
    "FullTimelineFigureArtifacts",
    "FullTimelineFigureInputError",
    "write_ootang_v3_full_timeline_figure",
]
