"""Render the all-station Ootang v3 displacement-and-state diagnostic.

Each small multiple aligns one station's cumulative displacement with five
categorical strips for all 514 v3 result dates.  The displayed states use the
target-date observation after forecast issuance, so this is a non-formal rule
audit rather than warning-lead-time or event-independent performance evidence.
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
from matplotlib.ticker import MaxNLocator

from warning import operational_v3_figure_support as figure_support
from warning.draft_evidence import FileReplacement, promote_staged_files

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = ROOT / "config" / "ootang_operational_run.v3.draft.json"
DEFAULT_FIGURE_SPEC_PATH = (
    ROOT / "config" / "ootang_operational_v3_station_diagnostic.v1.json"
)
DEFAULT_OUTPUT_DIR = ROOT / "figures" / "warning_operational_draft_v3"

FIGURE_STEM = "ootang_v3_all_station_combined_diagnostic"
MANIFEST_FILENAME = f"{FIGURE_STEM}_manifest.json"
ARTIFACT_STATUS = "operational_draft_not_formal"

LEVEL_NAMES = figure_support.LEVEL_NAMES
LEVEL_COLORS = figure_support.LEVEL_COLORS
LEVEL_ABBREVIATIONS = {
    "green": "G",
    "blue": "B",
    "yellow": "Y",
    "orange": "O",
    "red": "R",
}
DELTA_V_STATES = ("negative", "near_zero", "positive")
DELTA_V_COLORS = figure_support.DELTA_V_COLORS
DELTA_V_SYMBOLS = figure_support.DELTA_V_SYMBOLS
STRIP_LABELS = ("I", "V", "ΔV", "T", "F")

FIGURE_RC_PARAMS = {
    **figure_support.BASE_FIGURE_RC_PARAMS,
    "svg.hashsalt": "ootang-operational-v3-all-station-combined-v1",
    "axes.linewidth": 0.6,
}

EXPECTED_FIELD_MAPPINGS = {
    "cumulative_displacement": "actual",
    "interval_level": "interval_level",
    "velocity_level": "velocity_level",
    "delta_v_trend": "delta_v_state",
    "tangent_angle_level": "tangent_angle_level",
    "final_candidate_level": "candidate_level",
}
LEVEL_FIELDS = (
    "interval_level",
    "velocity_level",
    "tangent_angle_level",
    "candidate_level",
)
REQUIRED_COLUMNS = {
    "date",
    "station",
    "split",
    "actual",
    "interval_level",
    "velocity_level",
    "delta_v_state",
    "tangent_angle_level",
    "candidate_level",
    "formal_warning_output",
    "vajont_used",
}


class StationCombinedDiagnosticInputError(ValueError):
    """Raised when the combined diagnostic cannot be traced or completed."""


@dataclass(frozen=True)
class StationCombinedDiagnosticArtifacts:
    """Paths for one atomically promoted station-diagnostic bundle."""

    svg_path: Path
    pdf_path: Path
    png_path: Path
    manifest_path: Path


@dataclass(frozen=True)
class _StationDiagnosticData:
    rows: pd.DataFrame
    dates: tuple[str, ...]
    splits: tuple[str, ...]
    stations: tuple[str, ...]
    station_blocks: dict[str, str]
    actual_by_station: dict[str, np.ndarray]
    strips_by_station: dict[str, np.ndarray]


def _require_false_flags(rows: pd.DataFrame) -> None:
    if not bool(rows["formal_warning_output"].eq(False).all()):
        raise StationCombinedDiagnosticInputError(
            "Station timeline unexpectedly claims formal output."
        )
    if not bool(rows["vajont_used"].eq(False).all()):
        raise StationCombinedDiagnosticInputError(
            "Station timeline unexpectedly uses Vajont."
        )


def _require_contract(spec: dict[str, Any]) -> dict[str, Any]:
    if (
        spec.get("case") != "ootang"
        or spec.get("formal_warning_output") is not False
        or spec.get("vajont_used") is not False
        or spec.get("artifact_role")
        != "observed_after_forecast_all_station_combined_diagnostic"
        or spec.get("timing_semantics")
        != "target_observation_available_before_state_assignment"
    ):
        raise StationCombinedDiagnosticInputError(
            "Combined-diagnostic specification must remain non-formal, "
            "observed-after-forecast, and Ootang-only."
        )
    if spec.get("field_mappings") != EXPECTED_FIELD_MAPPINGS:
        raise StationCombinedDiagnosticInputError(
            "Combined-diagnostic field mappings drifted from the v1 contract."
        )
    if spec.get("warning_level_range") != [0, 4]:
        raise StationCombinedDiagnosticInputError(
            "Combined-diagnostic warning levels must remain integers 0 through 4."
        )
    if tuple(spec.get("delta_v_states", ())) != DELTA_V_STATES:
        raise StationCombinedDiagnosticInputError(
            "Combined-diagnostic delta-V state order drifted."
        )
    contract = spec.get("figure_contract")
    if not isinstance(contract, dict):
        raise StationCombinedDiagnosticInputError(
            "Combined-diagnostic specification lacks a figure contract."
        )
    if contract.get("station_layout") != "four_rows_by_two_columns":
        raise StationCombinedDiagnosticInputError(
            "Combined-diagnostic station layout must remain four-by-two."
        )
    if (
        contract.get("final_width_mm") != 183
        or contract.get("final_height_mm") != 230
    ):
        raise StationCombinedDiagnosticInputError(
            "Combined-diagnostic figure dimensions drifted from the v1 contract."
        )
    if tuple(contract.get("strip_order", ())) != (
        "interval_level",
        "velocity_level",
        "delta_v_trend",
        "tangent_angle_level",
        "final_candidate_level",
    ):
        raise StationCombinedDiagnosticInputError(
            "Combined-diagnostic categorical strip order drifted."
        )
    return contract


def _integer_levels(rows: pd.DataFrame, column: str) -> pd.Series:
    numeric = pd.to_numeric(rows[column], errors="coerce")
    valid = numeric.between(0, 4) & np.isclose(numeric, np.round(numeric))
    if not bool(valid.all()):
        raise StationCombinedDiagnosticInputError(
            f"Station timeline contains invalid {column} values."
        )
    return numeric.astype(int)


def _prepare_station_data(
    *,
    rows: pd.DataFrame,
    profile: dict[str, Any],
    spec: dict[str, Any],
) -> _StationDiagnosticData:
    figure_support.require_columns(
        rows,
        REQUIRED_COLUMNS,
        label="station timeline",
        error_type=StationCombinedDiagnosticInputError,
    )
    _require_false_flags(rows)
    _require_contract(spec)

    stations, station_blocks = figure_support.station_layout(
        profile,
        error_type=StationCombinedDiagnosticInputError,
    )
    station_order = tuple(stations)

    ordered = rows.copy()
    ordered["date"] = ordered["date"].astype(str)
    ordered["station"] = ordered["station"].astype(str)
    try:
        ordered["_parsed_date"] = pd.to_datetime(ordered["date"], errors="raise")
    except (TypeError, ValueError) as exc:
        raise StationCombinedDiagnosticInputError(
            "Station timeline contains an invalid date."
        ) from exc
    if set(ordered["station"]) != set(station_order):
        raise StationCombinedDiagnosticInputError(
            "Station timeline does not contain the frozen eight-station set."
        )
    if ordered[["date", "station"]].duplicated().any():
        raise StationCombinedDiagnosticInputError(
            "Station timeline contains a duplicated station-date pair."
        )

    date_frame = (
        ordered[["date", "_parsed_date", "split"]]
        .drop_duplicates()
        .sort_values("_parsed_date", kind="stable")
        .reset_index(drop=True)
    )
    if date_frame["date"].duplicated().any():
        raise StationCombinedDiagnosticInputError(
            "A result date has inconsistent split labels."
        )
    dates = tuple(date_frame["date"])
    splits = tuple(date_frame["split"].astype(str))
    expected_splits = tuple(spec.get("expected_splits", ()))
    if expected_splits != ("calibration", "test") or set(splits) != set(
        expected_splits
    ):
        raise StationCombinedDiagnosticInputError(
            "Combined diagnostic must contain calibration and test result splits."
        )
    transitions = sum(previous != current for previous, current in pairwise(splits))
    if transitions != 1 or splits[0] != "calibration" or splits[-1] != "test":
        raise StationCombinedDiagnosticInputError(
            "Combined diagnostic must preserve one calibration-to-test transition."
        )

    expected_counts = {
        "date_count": len(dates),
        "station_count": len(station_order),
        "station_row_count": len(ordered),
    }
    for name, actual in expected_counts.items():
        configured = spec.get(f"expected_{name}")
        if isinstance(configured, bool) or not isinstance(configured, int):
            raise StationCombinedDiagnosticInputError(
                f"Combined-diagnostic specification must declare expected_{name}."
            )
        if configured != actual:
            raise StationCombinedDiagnosticInputError(
                f"Combined-diagnostic {name} drifted: expected {configured}, "
                f"observed {actual}."
            )
    if len(ordered) != len(dates) * len(station_order):
        raise StationCombinedDiagnosticInputError(
            "Station timeline must contain exactly one row per station and date."
        )
    if not dates:
        raise StationCombinedDiagnosticInputError("Station timeline is empty.")
    if (
        dates[0] != spec.get("expected_start_date")
        or dates[-1] != spec.get("expected_end_date")
    ):
        raise StationCombinedDiagnosticInputError(
            "Combined-diagnostic date bounds drifted from the frozen snapshot."
        )

    actual = pd.to_numeric(ordered["actual"], errors="coerce")
    if not bool(np.isfinite(actual.to_numpy(dtype=float)).all()):
        raise StationCombinedDiagnosticInputError(
            "Station timeline contains a missing or non-finite displacement."
        )
    ordered["actual"] = actual.astype(float)
    for field in LEVEL_FIELDS:
        ordered[field] = _integer_levels(ordered, field)
    delta_states = ordered["delta_v_state"].astype(str)
    if not bool(delta_states.isin(DELTA_V_STATES).all()):
        raise StationCombinedDiagnosticInputError(
            "Station timeline contains an invalid delta-V state."
        )
    ordered["delta_v_state"] = delta_states

    station_rank = {station: index for index, station in enumerate(station_order)}
    ordered["_station_rank"] = ordered["station"].map(station_rank)
    ordered = ordered.sort_values(
        ["_station_rank", "_parsed_date"], kind="stable"
    ).reset_index(drop=True)
    delta_codes = {
        state: len(LEVEL_NAMES) + index for index, state in enumerate(DELTA_V_STATES)
    }
    actual_by_station: dict[str, np.ndarray] = {}
    strips_by_station: dict[str, np.ndarray] = {}
    for station in station_order:
        station_rows = ordered.loc[ordered["station"].eq(station)]
        if tuple(station_rows["date"]) != dates:
            raise StationCombinedDiagnosticInputError(
                f"Station {station} does not cover the complete frozen date axis."
            )
        actual_by_station[station] = station_rows["actual"].to_numpy(dtype=float)
        strips_by_station[station] = np.vstack(
            (
                station_rows["interval_level"].to_numpy(dtype=float),
                station_rows["velocity_level"].to_numpy(dtype=float),
                station_rows["delta_v_state"].map(delta_codes).to_numpy(dtype=float),
                station_rows["tangent_angle_level"].to_numpy(dtype=float),
                station_rows["candidate_level"].to_numpy(dtype=float),
            )
        )

    return _StationDiagnosticData(
        rows=ordered.drop(columns=["_parsed_date", "_station_rank"]),
        dates=dates,
        splits=splits,
        stations=station_order,
        station_blocks=station_blocks,
        actual_by_station=actual_by_station,
        strips_by_station=strips_by_station,
    )


def _date_ticks(dates: tuple[str, ...], count: int = 5) -> tuple[np.ndarray, list[str]]:
    indices = np.unique(
        np.rint(np.linspace(0, len(dates) - 1, min(count, len(dates)))).astype(int)
    )
    labels = [pd.Timestamp(dates[index]).strftime("%Y-%m") for index in indices]
    return indices, labels


def _split_boundary(splits: tuple[str, ...]) -> float:
    transition = next(
        index
        for index in range(1, len(splits))
        if splits[index] != splits[index - 1]
    )
    return transition - 0.5


def _render_figure(
    data: _StationDiagnosticData,
    *,
    width_mm: float,
    height_mm: float,
) -> plt.Figure:
    palette = [LEVEL_COLORS[name] for name in LEVEL_NAMES] + [
        DELTA_V_COLORS[state] for state in DELTA_V_STATES
    ]
    cmap = ListedColormap(palette)
    norm = BoundaryNorm(np.arange(-0.5, len(palette) + 0.5, 1), cmap.N)
    ticks, tick_labels = _date_ticks(data.dates)
    boundary = _split_boundary(data.splits)

    figure = plt.figure(
        figsize=(width_mm / 25.4, height_mm / 25.4), facecolor="white"
    )
    outer = figure.add_gridspec(
        4,
        2,
        left=0.08,
        right=0.985,
        bottom=0.12,
        top=0.84,
        hspace=0.56,
        wspace=0.24,
    )
    for index, station in enumerate(data.stations):
        cell = outer[index // 2, index % 2].subgridspec(
            2, 1, height_ratios=(2.2, 1.45), hspace=0.04
        )
        line_ax = figure.add_subplot(cell[0, 0])
        strip_ax = figure.add_subplot(cell[1, 0], sharex=line_ax)

        line_ax.plot(
            np.arange(len(data.dates)),
            data.actual_by_station[station],
            color="#2F2F2F",
            linewidth=0.75,
        )
        line_ax.axvline(boundary, color="#3A3A3A", linewidth=0.6, linestyle="--")
        line_ax.set_xlim(-0.5, len(data.dates) - 0.5)
        line_ax.yaxis.set_major_locator(MaxNLocator(nbins=3))
        line_ax.tick_params(axis="y", labelsize=5.2, length=2, pad=1.5)
        line_ax.tick_params(axis="x", bottom=False, labelbottom=False)
        line_ax.set_ylabel("U (mm)", fontsize=5.3, labelpad=2)
        panel = chr(ord("a") + index)
        line_ax.set_title(
            f"{panel}  {station} ({data.station_blocks[station]})",
            loc="left",
            fontsize=7,
            fontweight="bold",
            pad=3,
        )
        line_ax.spines["bottom"].set_color("#B0B0B0")
        line_ax.spines["left"].set_color("#808080")
        if index == 0:
            line_ax.text(
                boundary,
                1.04,
                "calibration | test",
                transform=line_ax.get_xaxis_transform(),
                ha="center",
                va="bottom",
                fontsize=5.2,
                color="#303030",
            )

        strip_ax.imshow(
            data.strips_by_station[station],
            aspect="auto",
            cmap=cmap,
            norm=norm,
            interpolation="none",
            rasterized=True,
        )
        strip_ax.axvline(boundary, color="#3A3A3A", linewidth=0.6, linestyle="--")
        strip_ax.set_yticks(np.arange(len(STRIP_LABELS)))
        strip_ax.set_yticklabels(STRIP_LABELS, fontsize=5.3)
        strip_ax.tick_params(axis="y", length=0, pad=2)
        strip_ax.set_xticks(ticks)
        show_x = index // 2 == 3
        strip_ax.set_xticklabels(
            tick_labels if show_x else [], rotation=28, ha="right", fontsize=5.1
        )
        strip_ax.tick_params(axis="x", length=2 if show_x else 0, pad=1.5)
        for spine in strip_ax.spines.values():
            spine.set_visible(False)

    figure.suptitle(
        "Ootang v3 all-station displacement and warning-state diagnostic",
        x=0.06,
        y=0.975,
        ha="left",
        fontsize=10,
        fontweight="bold",
    )
    figure.text(
        0.06,
        0.945,
        "OPERATIONAL DRAFT • NOT FORMAL • OBSERVED-AFTER-FORECAST",
        ha="left",
        va="center",
        fontsize=6.5,
        color="#8B2E2E",
        fontweight="bold",
    )
    figure.text(
        0.06,
        0.912,
        (
            "Each panel aligns cumulative displacement with I=interval, V=velocity, "
            "ΔV=trend, T=tangent angle, and F=final candidate state for all 514 dates."
        ),
        ha="left",
        va="center",
        fontsize=5.8,
        color="#303030",
    )
    level_handles = [
        Patch(
            facecolor=LEVEL_COLORS[name],
            edgecolor="#303030",
            linewidth=0.4,
            label=f"{LEVEL_ABBREVIATIONS[name]} {name}",
        )
        for name in LEVEL_NAMES
    ]
    delta_handles = [
        Patch(
            facecolor=DELTA_V_COLORS[state],
            edgecolor="#303030",
            linewidth=0.4,
            label=f"ΔV {DELTA_V_SYMBOLS[state]} {state.replace('_', ' ')}",
        )
        for state in DELTA_V_STATES
    ]
    figure.legend(
        handles=[*level_handles, *delta_handles],
        loc="lower center",
        bbox_to_anchor=(0.52, 0.055),
        ncol=4,
        fontsize=5.4,
        handlelength=1.35,
        columnspacing=0.9,
        labelspacing=0.7,
    )
    figure.text(
        0.06,
        0.018,
        (
            "Target-date observations are available before these categorical states "
            "are assigned; the figure does not demonstrate lead time or formal warning validity."
        ),
        ha="left",
        va="center",
        fontsize=5.4,
        color="#555555",
    )
    return figure


def _count_levels(rows: pd.DataFrame, column: str) -> dict[str, int]:
    values = rows[column].to_numpy(dtype=int)
    return {
        name: int(np.sum(values == index))
        for index, name in enumerate(LEVEL_NAMES)
    }


def _plot_data_sha256(data: _StationDiagnosticData) -> str:
    return figure_support.canonical_json_sha256(
        {
            "dates": list(data.dates),
            "splits": list(data.splits),
            "stations": list(data.stations),
            "station_blocks": data.station_blocks,
            "actual": {
                station: data.actual_by_station[station].tolist()
                for station in data.stations
            },
            "categorical_strips": {
                station: data.strips_by_station[station].astype(int).tolist()
                for station in data.stations
            },
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
    data: _StationDiagnosticData,
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
    delta_counts = data.rows["delta_v_state"].value_counts()
    return {
        "schema_version": "ootang_operational_v3_station_combined_figure_v1",
        "artifact_kind": "ootang_operational_v3_all_station_combined_diagnostic",
        "artifact_status": ARTIFACT_STATUS,
        "artifact_role": figure_spec["artifact_role"],
        "formal_warning_output": False,
        "vajont_used": False,
        "case": "ootang",
        "observation_timing": {
            "status": "observed_after_forecast",
            "meaning": (
                "Each categorical state is assigned after the target-date "
                "observation is available; no warning lead time is claimed."
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
            "figure_contract": contract,
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
                "n_rows": len(data.rows),
                "core_manifest_match": True,
                "displayed": True,
            },
            "site_timeline": {
                "path": figure_support.manifest_path(site_path),
                "sha256": site_sha256,
                "core_manifest_match": True,
                "displayed": False,
                "role": "core_run_provenance_validation_only",
            },
        },
        "field_mappings": EXPECTED_FIELD_MAPPINGS,
        "timeline_coverage": {
            "start_date": data.dates[0],
            "end_date": data.dates[-1],
            "date_count": len(data.dates),
            "station_count": len(data.stations),
            "station_row_count": len(data.rows),
            "splits": list(dict.fromkeys(data.splits)),
            "complete_station_date_grid": True,
        },
        "display_layout": {
            "station_grid": "four_rows_by_two_columns",
            "station_order": list(data.stations),
            "station_blocks": data.station_blocks,
            "strip_order": contract["strip_order"],
            "strip_labels": list(STRIP_LABELS),
        },
        "state_counts": {
            **{field: _count_levels(data.rows, field) for field in LEVEL_FIELDS},
            "delta_v_state": {
                state: int(delta_counts.get(state, 0)) for state in DELTA_V_STATES
            },
        },
        "plot_data_sha256": _plot_data_sha256(data),
        "rendering": {
            "backend": str(matplotlib.get_backend()).lower(),
            "matplotlib_version": matplotlib.__version__,
            "python_version": sys.version.split()[0],
            "width_mm": contract["final_width_mm"],
            "height_mm": contract["final_height_mm"],
            "png_dpi": 300,
            "svg_text_editable": True,
            "palette": {
                "warning_levels": LEVEL_COLORS,
                "delta_v_states": DELTA_V_COLORS,
            },
        },
        "outputs": output_records,
        "not_claimed": [
            "formal_warning_output",
            "warning_lead_time",
            "event_independent_warning_performance",
            "causal_driver_attribution",
            "Vajont_external_case",
        ],
    }


def write_ootang_v3_station_combined_diagnostic(
    *,
    station_timeline_path: Path,
    site_timeline_path: Path,
    run_manifest_path: Path,
    profile_path: Path = DEFAULT_PROFILE_PATH,
    figure_spec_path: Path = DEFAULT_FIGURE_SPEC_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> StationCombinedDiagnosticArtifacts:
    """Validate, render, and atomically promote the all-station diagnostic."""

    station_path = Path(station_timeline_path).resolve()
    site_path = Path(site_timeline_path).resolve()
    core_manifest_path = Path(run_manifest_path).resolve()
    operational_profile_path = Path(profile_path).resolve()
    spec_path = Path(figure_spec_path).resolve()
    target_dir = Path(output_dir).resolve()

    profile, _profile_payload = figure_support.read_json_snapshot(
        operational_profile_path,
        label="operational profile",
        error_type=StationCombinedDiagnosticInputError,
    )
    figure_spec, figure_spec_payload = figure_support.read_json_snapshot(
        spec_path,
        label="combined-diagnostic specification",
        error_type=StationCombinedDiagnosticInputError,
    )
    core_manifest, core_manifest_payload = figure_support.read_json_snapshot(
        core_manifest_path,
        label="core run manifest",
        error_type=StationCombinedDiagnosticInputError,
    )
    station_rows, station_payload = figure_support.read_csv_snapshot(
        station_path,
        label="station timeline",
        error_type=StationCombinedDiagnosticInputError,
    )
    _site_rows, site_payload = figure_support.read_csv_snapshot(
        site_path,
        label="site timeline",
        error_type=StationCombinedDiagnosticInputError,
    )
    renderer_payload = Path(__file__).resolve().read_bytes()
    shared_support_payload = Path(figure_support.__file__).resolve().read_bytes()

    figure_support.validate_run_provenance(
        manifest=core_manifest,
        station_sha256=figure_support.sha256_bytes(station_payload),
        site_sha256=figure_support.sha256_bytes(site_payload),
        profile=profile,
        error_type=StationCombinedDiagnosticInputError,
    )
    contract = _require_contract(figure_spec)
    data = _prepare_station_data(rows=station_rows, profile=profile, spec=figure_spec)

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    targets = (
        target_dir / f"{FIGURE_STEM}.svg",
        target_dir / f"{FIGURE_STEM}.pdf",
        target_dir / f"{FIGURE_STEM}.png",
    )
    manifest_target = target_dir / MANIFEST_FILENAME
    with tempfile.TemporaryDirectory(
        prefix=f".{target_dir.name}.station-combined-",
        dir=target_dir.parent,
    ) as directory:
        staging_dir = Path(directory)
        with plt.rc_context(FIGURE_RC_PARAMS):
            existing_figures = set(plt.get_fignums())
            try:
                figure = _render_figure(
                    data,
                    width_mm=float(contract["final_width_mm"]),
                    height_mm=float(contract["final_height_mm"]),
                )
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
                    error_type=StationCombinedDiagnosticInputError,
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

    return StationCombinedDiagnosticArtifacts(
        svg_path=targets[0],
        pdf_path=targets[1],
        png_path=targets[2],
        manifest_path=manifest_target,
    )


__all__ = [
    "DEFAULT_FIGURE_SPEC_PATH",
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_PROFILE_PATH",
    "StationCombinedDiagnosticArtifacts",
    "StationCombinedDiagnosticInputError",
    "write_ootang_v3_station_combined_diagnostic",
]
