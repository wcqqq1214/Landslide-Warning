"""Render a traceable representative-day audit for the Ootang v3 rules.

The figure is a post-observation diagnostic of the non-formal operational draft.
It is not an event-independent performance, lead-time, or formal-warning result.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch

from warning.draft_evidence import FileReplacement, promote_staged_files

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = ROOT / "config" / "ootang_operational_run.v3.draft.json"
DEFAULT_FIGURE_SPEC_PATH = (
    ROOT / "config" / "ootang_operational_v3_typical_days.v1.json"
)
DEFAULT_OUTPUT_DIR = ROOT / "figures" / "warning_operational_draft_v3"

FIGURE_STEM = "ootang_v3_typical_days"
MANIFEST_FILENAME = f"{FIGURE_STEM}_manifest.json"
ARTIFACT_STATUS = "operational_draft_not_formal"
EXPECTED_PROFILE_ID = "ootang-operational-spatial-v3"
EXPECTED_PROFILE_VERSION = "3.0-draft"

FIGURE_RC_PARAMS = {
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "DejaVu Sans", "Liberation Sans"],
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "svg.hashsalt": "ootang-operational-v3-typical-days-v1",
    "font.size": 6,
    "axes.linewidth": 0.7,
    "axes.spines.right": False,
    "axes.spines.top": False,
    "legend.frameon": False,
}

REPRESENTATIVE_DAY_RULE_IDS = (
    "unconfirmed_yellow",
    "localized_blue_attention",
    "unconfirmed_red",
    "confirmed_yellow_local_red",
    "confirmed_orange",
    "confirmed_red",
)

LEVEL_COLORS = {
    "green": "#6FA86B",
    "blue": "#4C78A8",
    "yellow": "#E8C95A",
    "orange": "#DF8C3F",
    "red": "#C44E52",
}
LEVEL_ABBREVIATIONS = {
    "green": "G",
    "blue": "B",
    "yellow": "Y",
    "orange": "O",
    "red": "R",
}
DELTA_V_COLORS = {
    "negative": "#6B8FB3",
    "near_zero": "#D7D7D7",
    "positive": "#C96A62",
}
DELTA_V_SYMBOLS = {"negative": "−", "near_zero": "0", "positive": "+"}

SITE_REQUIRED_COLUMNS = {
    "date",
    "split",
    "site_fusion_status",
    "site_confirmed_level",
    "site_confirmed_color",
    "local_max_candidate_level",
    "local_max_candidate_color",
    "local_attention_status",
    "site_fusion_reason",
    "contributing_stations",
    "contributing_blocks",
    "local_max_candidate_stations",
    "local_max_candidate_blocks",
    "formal_warning_output",
    "vajont_used",
}
STATION_REQUIRED_COLUMNS = {
    "date",
    "station",
    "interval_level",
    "interval_color",
    "kinematic_level",
    "kinematic_color",
    "candidate_level",
    "delta_v_state",
    "formal_warning_output",
    "vajont_used",
}


class TypicalDayFigureInputError(ValueError):
    """Raised when the figure would not be traceable to a valid v3 snapshot."""


@dataclass(frozen=True)
class TypicalDayFigureArtifacts:
    """Paths for one atomically promoted representative-day figure bundle."""

    svg_path: Path
    pdf_path: Path
    png_path: Path
    manifest_path: Path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _manifest_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_json_snapshot(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TypicalDayFigureInputError(f"Cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise TypicalDayFigureInputError(f"{label} must contain a JSON object.")
    return value, payload


def _read_csv_snapshot(path: Path, *, label: str) -> tuple[pd.DataFrame, bytes]:
    try:
        payload = path.read_bytes()
        frame = pd.read_csv(io.BytesIO(payload), dtype={"date": str})
    except (OSError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise TypicalDayFigureInputError(f"Cannot read {label}: {path}") from exc
    return frame, payload


def _require_columns(frame: pd.DataFrame, required: set[str], *, label: str) -> None:
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise TypicalDayFigureInputError(
            f"{label} is missing required columns: {', '.join(missing)}"
        )


def _rule_mask(rows: pd.DataFrame, rule_id: str) -> pd.Series:
    if rule_id == "unconfirmed_yellow":
        return rows["site_fusion_status"].eq(
            "candidate_not_site_confirmed"
        ) & rows["local_max_candidate_color"].eq("yellow")
    if rule_id == "localized_blue_attention":
        return rows["local_attention_status"].eq("localized_blue_attention")
    if rule_id == "unconfirmed_red":
        return rows["site_fusion_status"].eq(
            "candidate_not_site_confirmed"
        ) & rows["local_max_candidate_color"].eq("red") & (
            rows["station_count_orange"] + rows["station_count_red"]
        ).ge(3)
    if rule_id == "confirmed_yellow_local_red":
        return rows["site_confirmed_color"].eq("yellow") & rows[
            "local_max_candidate_color"
        ].eq("red")
    if rule_id == "confirmed_orange":
        return rows["site_confirmed_color"].eq("orange")
    if rule_id == "confirmed_red":
        return rows["site_confirmed_color"].eq("red")
    raise TypicalDayFigureInputError(
        f"Unsupported representative-day semantic rule: {rule_id}"
    )


def select_representative_days(
    site_rows: pd.DataFrame,
    rules: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """Select the earliest date satisfying each frozen semantic rule."""

    needed = {
        "date",
        "site_fusion_status",
        "site_confirmed_color",
        "local_max_candidate_color",
        "local_attention_status",
        "station_count_orange",
        "station_count_red",
    }
    _require_columns(site_rows, needed, label="site timeline")
    ordered = site_rows.copy()
    ordered["date"] = ordered["date"].astype(str)
    ordered = ordered.sort_values("date", kind="stable")
    rule_records = (
        [
            {"rule_id": rule_id, "label": rule_id.replace("_", " ")}
            for rule_id in REPRESENTATIVE_DAY_RULE_IDS
        ]
        if rules is None
        else rules
    )
    if not rule_records:
        raise TypicalDayFigureInputError(
            "Representative-day rule list must not be empty."
        )
    rule_ids = tuple(str(rule.get("rule_id")) for rule in rule_records)
    if rule_ids != REPRESENTATIVE_DAY_RULE_IDS:
        raise TypicalDayFigureInputError(
            "Representative-day rules must preserve the frozen v1 rule order."
        )

    selected: list[dict[str, Any]] = []
    for rule in rule_records:
        rule_id = str(rule["rule_id"])
        matches = ordered.loc[_rule_mask(ordered, rule_id)]
        if matches.empty:
            raise TypicalDayFigureInputError(
                f"No site date satisfies representative-day rule {rule_id}."
            )
        record = matches.iloc[0].to_dict()
        record["rule_id"] = rule_id
        record["rule_label"] = str(rule.get("label", rule_id))
        selected.append(record)
    return pd.DataFrame(selected)


def _values_match(actual: Any, expected: Any) -> bool:
    if expected is None:
        return bool(pd.isna(actual))
    if pd.isna(actual):
        return False
    return str(actual) == str(expected)


def _validate_frozen_expectations(
    selected: pd.DataFrame,
    rules: list[dict[str, Any]],
) -> None:
    fields = (
        "date",
        "split",
        "site_fusion_status",
        "site_confirmed_color",
        "local_max_candidate_color",
        "local_attention_status",
    )
    for (_, row), rule in zip(selected.iterrows(), rules):
        expected = rule.get("expected")
        if not isinstance(expected, dict):
            raise TypicalDayFigureInputError(
                f"Rule {rule['rule_id']} must freeze expected outcomes."
            )
        for field in fields:
            if field not in expected or not _values_match(row[field], expected[field]):
                raise TypicalDayFigureInputError(
                    f"Representative day {rule['rule_id']} drifted at {field}: "
                    f"expected {expected.get(field)!r}, observed {row[field]!r}."
                )


def _validate_run_provenance(
    *,
    manifest: dict[str, Any],
    station_sha256: str,
    site_sha256: str,
    profile: dict[str, Any],
) -> None:
    if manifest.get("formal_warning_output") is not False:
        raise TypicalDayFigureInputError("Core run must remain non-formal.")
    if manifest.get("vajont_used") is not False:
        raise TypicalDayFigureInputError("Core run unexpectedly used Vajont.")
    operational_profile = manifest.get("operational_profile")
    if not isinstance(operational_profile, dict):
        raise TypicalDayFigureInputError("Core run lacks an operational profile.")
    profile_sha = _canonical_json_sha256(profile)
    if (
        operational_profile.get("id") != EXPECTED_PROFILE_ID
        or operational_profile.get("version") != EXPECTED_PROFILE_VERSION
        or operational_profile.get("content_sha256") != profile_sha
    ):
        raise TypicalDayFigureInputError(
            "Core run does not match the frozen Ootang v3 profile."
        )

    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise TypicalDayFigureInputError("Core run lacks output provenance.")
    for key, source_sha256 in (
        ("station_timeline", station_sha256),
        ("site_timeline", site_sha256),
    ):
        record = outputs.get(key)
        if not isinstance(record, dict) or record.get("sha256") != source_sha256:
            raise TypicalDayFigureInputError(
                f"{key} does not match the core run manifest."
            )

    sources = manifest.get("implementation_sources")
    if not isinstance(sources, dict) or not sources:
        raise TypicalDayFigureInputError(
            "Core run lacks implementation-source fingerprints."
        )
    for name, source in sources.items():
        if not isinstance(source, dict) or not isinstance(source.get("path"), str):
            raise TypicalDayFigureInputError(
                f"Invalid implementation-source record: {name}."
            )
        source_path = Path(source["path"])
        if not source_path.is_absolute():
            source_path = ROOT / source_path
        if not source_path.is_file() or source.get("sha256") != _sha256_file(
            source_path
        ):
            raise TypicalDayFigureInputError(
                f"Core run implementation fingerprint is stale: {name}."
            )


def _station_layout(profile: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    site_fusion = profile.get("site_fusion")
    if not isinstance(site_fusion, dict):
        raise TypicalDayFigureInputError("Profile lacks site_fusion.")
    blocks = site_fusion.get("spatial_blocks")
    if not isinstance(blocks, dict) or tuple(blocks) != ("O1", "O2", "O3"):
        raise TypicalDayFigureInputError(
            "Profile must declare ordered O1/O2/O3 spatial blocks."
        )
    stations: list[str] = []
    station_blocks: dict[str, str] = {}
    for block, members in blocks.items():
        if not isinstance(members, list):
            raise TypicalDayFigureInputError(f"Spatial block {block} is invalid.")
        for station in members:
            name = str(station)
            stations.append(name)
            station_blocks[name] = str(block)
    if len(stations) != len(set(stations)) or len(stations) != 8:
        raise TypicalDayFigureInputError(
            "Typical-day figure requires eight unique Ootang stations."
        )
    return stations, station_blocks


def _selected_station_rows(
    station_rows: pd.DataFrame,
    selected_dates: list[str],
    station_order: list[str],
) -> pd.DataFrame:
    subset = station_rows.loc[
        station_rows["date"].astype(str).isin(selected_dates)
    ].copy()
    expected_count = len(selected_dates) * len(station_order)
    unique_count = subset[["date", "station"]].drop_duplicates().shape[0]
    if len(subset) != expected_count or unique_count != expected_count:
        raise TypicalDayFigureInputError(
            "Each representative date must contain exactly one row for every station."
        )
    actual_stations = set(subset["station"].astype(str))
    if actual_stations != set(station_order):
        raise TypicalDayFigureInputError(
            "Representative dates do not contain the frozen station set."
        )
    if subset["formal_warning_output"].astype(bool).any():
        raise TypicalDayFigureInputError("Station rows unexpectedly claim formal output.")
    if subset["vajont_used"].astype(bool).any():
        raise TypicalDayFigureInputError("Station rows unexpectedly use Vajont.")
    if not subset["delta_v_state"].isin(DELTA_V_COLORS).all():
        raise TypicalDayFigureInputError("Station rows contain an invalid delta-V state.")
    for column in ("interval_level", "kinematic_level", "candidate_level"):
        numeric = pd.to_numeric(subset[column], errors="coerce")
        if numeric.isna().any() or not numeric.between(0, 4).all():
            raise TypicalDayFigureInputError(
                f"Station rows contain invalid {column} values."
            )
    return subset


def _matrix(
    rows: pd.DataFrame,
    dates: list[str],
    stations: list[str],
    column: str,
) -> np.ndarray:
    indexed = rows.assign(date=rows["date"].astype(str)).set_index(
        ["date", "station"]
    )
    return np.asarray(
        [[indexed.loc[(date, station), column] for station in stations] for date in dates]
    )


def _style_matrix_axis(
    ax: plt.Axes,
    *,
    stations: list[str],
    blocks: dict[str, str],
    show_y_labels: bool,
    y_labels: list[str],
) -> None:
    ax.set_xticks(np.arange(len(stations)))
    ax.set_xticklabels(stations, rotation=45, ha="left", fontsize=5.5)
    ax.xaxis.tick_top()
    ax.tick_params(axis="x", length=0, pad=2)
    ax.set_yticks(np.arange(len(y_labels)))
    ax.set_yticklabels(y_labels if show_y_labels else [], fontsize=5.7)
    ax.tick_params(axis="y", length=0, pad=3)
    ax.set_xticks(np.arange(-0.5, len(stations), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(y_labels), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=0.6)
    ax.tick_params(which="minor", bottom=False, left=False)
    for boundary in (2.5, 5.5):
        ax.axvline(boundary, color="#303030", linewidth=1.2)
    block_centers: dict[str, list[int]] = {}
    for index, station in enumerate(stations):
        block_centers.setdefault(blocks[station], []).append(index)
    for block, indices in block_centers.items():
        ax.text(
            float(np.mean(indices)),
            -0.045,
            block,
            transform=ax.get_xaxis_transform(),
            ha="center",
            va="top",
            fontsize=5.8,
            fontweight="bold",
            clip_on=False,
        )
    for spine in ax.spines.values():
        spine.set_visible(False)


def _draw_level_matrix(
    ax: plt.Axes,
    values: np.ndarray,
    *,
    stations: list[str],
    blocks: dict[str, str],
    y_labels: list[str],
    show_y_labels: bool,
    title: str,
) -> None:
    colors = [LEVEL_COLORS[name] for name in LEVEL_COLORS]
    cmap = ListedColormap(colors)
    cmap.set_bad("#E7E7E7")
    ax.imshow(
        np.ma.masked_invalid(values.astype(float)),
        aspect="auto",
        cmap=cmap,
        norm=BoundaryNorm(np.arange(-0.5, 5.5, 1), cmap.N),
        interpolation="none",
    )
    names = tuple(LEVEL_COLORS)
    for row_index in range(values.shape[0]):
        for column_index in range(values.shape[1]):
            level = int(values[row_index, column_index])
            ax.text(
                column_index,
                row_index,
                LEVEL_ABBREVIATIONS[names[level]],
                ha="center",
                va="center",
                fontsize=5.2,
                color="white" if level in {1, 3, 4} else "#202020",
                fontweight="bold",
            )
    _style_matrix_axis(
        ax,
        stations=stations,
        blocks=blocks,
        show_y_labels=show_y_labels,
        y_labels=y_labels,
    )
    ax.set_title(title, fontsize=7, fontweight="bold", pad=28)


def _draw_delta_matrix(
    ax: plt.Axes,
    values: np.ndarray,
    *,
    stations: list[str],
    blocks: dict[str, str],
    y_labels: list[str],
) -> None:
    names = tuple(DELTA_V_COLORS)
    encoded = np.vectorize({name: index for index, name in enumerate(names)}.get)(
        values
    ).astype(float)
    cmap = ListedColormap([DELTA_V_COLORS[name] for name in names])
    ax.imshow(
        encoded,
        aspect="auto",
        cmap=cmap,
        norm=BoundaryNorm(np.arange(-0.5, 3.5, 1), cmap.N),
        interpolation="none",
    )
    for row_index in range(values.shape[0]):
        for column_index in range(values.shape[1]):
            state = str(values[row_index, column_index])
            ax.text(
                column_index,
                row_index,
                DELTA_V_SYMBOLS[state],
                ha="center",
                va="center",
                fontsize=6,
                color="white" if state != "near_zero" else "#202020",
                fontweight="bold",
            )
    _style_matrix_axis(
        ax,
        stations=stations,
        blocks=blocks,
        show_y_labels=False,
        y_labels=y_labels,
    )
    ax.set_title("c  ΔV state", fontsize=7, fontweight="bold", pad=28)


def _draw_dual_axis(ax: plt.Axes, selected: pd.DataFrame) -> None:
    values = selected[["site_confirmed_level", "local_max_candidate_level"]].apply(
        pd.to_numeric, errors="coerce"
    ).to_numpy(dtype=float)
    colors = [LEVEL_COLORS[name] for name in LEVEL_COLORS]
    cmap = ListedColormap(colors)
    cmap.set_bad("#E7E7E7")
    ax.imshow(
        np.ma.masked_invalid(values),
        aspect="auto",
        cmap=cmap,
        norm=BoundaryNorm(np.arange(-0.5, 5.5, 1), cmap.N),
        interpolation="none",
    )
    names = tuple(LEVEL_COLORS)
    for row_index in range(values.shape[0]):
        for column_index in range(values.shape[1]):
            value = values[row_index, column_index]
            label = "NC" if np.isnan(value) else LEVEL_ABBREVIATIONS[names[int(value)]]
            ax.text(
                column_index,
                row_index,
                label,
                ha="center",
                va="center",
                fontsize=5.5,
                color=(
                    "#303030"
                    if np.isnan(value) or int(value) in {0, 2}
                    else "white"
                ),
                fontweight="bold",
            )
    ax.set_xticks((0, 1))
    ax.set_xticklabels(("site", "local"), rotation=45, ha="left", fontsize=5.5)
    ax.xaxis.tick_top()
    ax.tick_params(axis="x", length=0, pad=2)
    ax.set_yticks([])
    ax.set_xticks(np.arange(-0.5, 2, 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(selected), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=0.6)
    ax.tick_params(which="minor", bottom=False, left=False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title("d  Dual axis", fontsize=7, fontweight="bold", pad=28)


def _split_values(value: Any) -> list[str]:
    if pd.isna(value) or str(value).strip() == "":
        return []
    return [part for part in str(value).split(";") if part]


def _candidate_support(
    station_rows: pd.DataFrame,
    *,
    date: str,
    threshold: int,
    station_blocks: dict[str, str],
) -> tuple[list[str], list[str]]:
    rows = station_rows.loc[station_rows["date"].astype(str).eq(date)]
    stations = rows.loc[
        pd.to_numeric(rows["candidate_level"], errors="coerce").ge(threshold),
        "station",
    ].astype(str).tolist()
    blocks = sorted({station_blocks[station] for station in stations})
    return stations, blocks


def _draw_support_panel(
    ax: plt.Axes,
    selected: pd.DataFrame,
    station_rows: pd.DataFrame,
    station_blocks: dict[str, str],
) -> None:
    ax.set_xlim(0, 1)
    ax.set_ylim(len(selected) - 0.5, -0.5)
    ax.axis("off")
    for row_index, (_, row) in enumerate(selected.iterrows()):
        confirmed_stations = _split_values(row["contributing_stations"])
        confirmed_blocks = _split_values(row["contributing_blocks"])
        if confirmed_stations:
            first_line = (
                f"confirm: {','.join(confirmed_stations)} "
                f"[{','.join(confirmed_blocks)}]"
            )
        else:
            threshold = 1 if row["local_max_candidate_color"] == "blue" else 2
            candidates, blocks = _candidate_support(
                station_rows,
                date=str(row["date"]),
                threshold=threshold,
                station_blocks=station_blocks,
            )
            first_line = (
                f"no confirm; ≥{LEVEL_ABBREVIATIONS[tuple(LEVEL_COLORS)[threshold]]}: "
                f"{','.join(candidates)} [{','.join(blocks)}]"
            )
        local_stations = _split_values(row["local_max_candidate_stations"])
        local_blocks = _split_values(row["local_max_candidate_blocks"])
        second_line = (
            f"local max: {','.join(local_stations)} [{','.join(local_blocks)}]"
        )
        attention = (
            "  • localized blue attention"
            if row["local_attention_status"] == "localized_blue_attention"
            else ""
        )
        ax.text(
            0.0,
            row_index - 0.11,
            first_line,
            ha="left",
            va="center",
            fontsize=5.25,
            color="#303030",
        )
        ax.text(
            0.0,
            row_index + 0.18,
            second_line + attention,
            ha="left",
            va="center",
            fontsize=5.05,
            color="#606060",
        )
    ax.set_title("e  Spatial support", fontsize=7, fontweight="bold", pad=28)


def _render_figure(
    *,
    station_rows: pd.DataFrame,
    selected: pd.DataFrame,
    stations: list[str],
    station_blocks: dict[str, str],
) -> plt.Figure:
    dates = selected["date"].astype(str).tolist()
    y_labels = [
        f"{row.date}\n{row.rule_label}"
        for row in selected[["date", "rule_label"]].itertuples(index=False)
    ]
    interval = _matrix(station_rows, dates, stations, "interval_level").astype(float)
    kinematic = _matrix(station_rows, dates, stations, "kinematic_level").astype(
        float
    )
    delta_v = _matrix(station_rows, dates, stations, "delta_v_state")

    fig = plt.figure(figsize=(183 / 25.4, 135 / 25.4), facecolor="white")
    grid = fig.add_gridspec(
        1,
        5,
        width_ratios=(2.5, 2.5, 2.5, 0.85, 3.7),
        left=0.11,
        right=0.992,
        bottom=0.145,
        top=0.795,
        wspace=0.22,
    )
    axes = [fig.add_subplot(grid[0, index]) for index in range(5)]
    _draw_level_matrix(
        axes[0],
        interval,
        stations=stations,
        blocks=station_blocks,
        y_labels=y_labels,
        show_y_labels=True,
        title="a  Interval evidence",
    )
    _draw_level_matrix(
        axes[1],
        kinematic,
        stations=stations,
        blocks=station_blocks,
        y_labels=y_labels,
        show_y_labels=False,
        title="b  Kinematic evidence",
    )
    _draw_delta_matrix(
        axes[2],
        delta_v,
        stations=stations,
        blocks=station_blocks,
        y_labels=y_labels,
    )
    _draw_dual_axis(axes[3], selected)
    _draw_support_panel(axes[4], selected, station_rows, station_blocks)

    fig.suptitle(
        "Ootang v3 spatial-rule audit — representative days",
        x=0.073,
        y=0.965,
        ha="left",
        fontsize=9,
        fontweight="bold",
    )
    fig.text(
        0.073,
        0.915,
        (
            "NON-FORMAL DRAFT • earliest date per frozen semantic rule • "
            "post-observation illustration, not warning-performance evidence"
        ),
        ha="left",
        va="center",
        fontsize=6.2,
        color="#8B2E2E",
    )
    level_handles = [
        Patch(
            facecolor=color,
            edgecolor="#303030",
            linewidth=0.4,
            label=f"{LEVEL_ABBREVIATIONS[name]} {name}",
        )
        for name, color in LEVEL_COLORS.items()
    ]
    delta_handles = [
        Patch(
            facecolor=color,
            edgecolor="#303030",
            linewidth=0.4,
            label=f"ΔV {DELTA_V_SYMBOLS[name]} {name.replace('_', ' ')}",
        )
        for name, color in DELTA_V_COLORS.items()
    ]
    missing_handle = Patch(
        facecolor="#E7E7E7",
        edgecolor="#303030",
        linewidth=0.4,
        label="NC not site-confirmed",
    )
    fig.legend(
        handles=[*level_handles, missing_handle, *delta_handles],
        loc="lower center",
        bbox_to_anchor=(0.52, 0.027),
        ncol=5,
        fontsize=5.5,
        handlelength=1.4,
        columnspacing=1.0,
    )
    return fig


def _save_figure(fig: plt.Figure, directory: Path) -> tuple[Path, Path, Path]:
    svg_path = directory / f"{FIGURE_STEM}.svg"
    pdf_path = directory / f"{FIGURE_STEM}.pdf"
    png_path = directory / f"{FIGURE_STEM}.png"
    fig.savefig(
        svg_path,
        format="svg",
        metadata={"Date": None, "Creator": "Landslide-Warning"},
    )
    svg_text = svg_path.read_text(encoding="utf-8")
    svg_path.write_text(
        "\n".join(line.rstrip() for line in svg_text.splitlines()) + "\n",
        encoding="utf-8",
    )
    fig.savefig(
        pdf_path,
        format="pdf",
        metadata={
            "CreationDate": None,
            "ModDate": None,
            "Creator": "Landslide-Warning",
        },
    )
    fig.savefig(
        png_path,
        format="png",
        dpi=300,
        metadata={"Software": "Landslide-Warning"},
    )
    if not svg_path.read_bytes().lstrip().startswith(b"<?xml"):
        raise TypicalDayFigureInputError("SVG export is invalid.")
    if not pdf_path.read_bytes().startswith(b"%PDF-"):
        raise TypicalDayFigureInputError("PDF export is invalid.")
    if not png_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
        raise TypicalDayFigureInputError("PNG export is invalid.")
    return svg_path, pdf_path, png_path


def _plotted_subset_sha256(
    station_rows: pd.DataFrame,
    selected: pd.DataFrame,
) -> str:
    station_columns = sorted(STATION_REQUIRED_COLUMNS)
    site_columns = sorted(SITE_REQUIRED_COLUMNS | {"rule_id", "rule_label"})
    station_records = json.loads(
        station_rows.sort_values(["date", "station"], kind="stable")[station_columns]
        .to_json(orient="records")
    )
    site_records = json.loads(
        selected.sort_values("date", kind="stable")[site_columns].to_json(
            orient="records"
        )
    )
    return _canonical_json_sha256(
        {"station_records": station_records, "site_records": site_records}
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
    selected: pd.DataFrame,
    station_subset: pd.DataFrame,
    figure_spec_sha256: str,
    run_manifest_sha256: str,
    station_sha256: str,
    site_sha256: str,
    station_row_count: int,
    site_row_count: int,
    renderer_sha256: str,
    output_targets: tuple[Path, Path, Path],
    output_sources: tuple[Path, Path, Path],
) -> dict[str, Any]:
    representative_days = []
    fields = (
        "rule_id",
        "rule_label",
        "date",
        "split",
        "site_fusion_status",
        "site_confirmed_color",
        "local_max_candidate_color",
        "local_attention_status",
        "site_fusion_reason",
        "contributing_stations",
        "contributing_blocks",
        "local_max_candidate_stations",
        "local_max_candidate_blocks",
    )
    for _, row in selected.iterrows():
        representative_days.append(
            {
                field: (None if pd.isna(row[field]) else row[field])
                for field in fields
            }
        )
    output_records = {
        path.suffix.lstrip("."): {
            "path": _manifest_path(path),
            "sha256": _sha256_file(source),
            "n_bytes": source.stat().st_size,
        }
        for path, source in zip(output_targets, output_sources)
    }
    return {
        "schema_version": "ootang_operational_v3_typical_days_figure_v1",
        "artifact_kind": "ootang_operational_v3_representative_day_rule_audit",
        "artifact_status": ARTIFACT_STATUS,
        "artifact_role": figure_spec["artifact_role"],
        "formal_warning_output": False,
        "vajont_used": False,
        "case": "ootang",
        "operational_profile": {
            "id": profile["profile_id"],
            "version": profile["profile_version"],
            "path": _manifest_path(profile_path),
            "content_sha256": _canonical_json_sha256(profile),
        },
        "figure_spec": {
            "id": figure_spec["figure_id"],
            "version": figure_spec["figure_version"],
            "path": _manifest_path(figure_spec_path),
            "sha256": figure_spec_sha256,
            "selection_method": figure_spec["selection_method"],
            "figure_contract": figure_spec["figure_contract"],
        },
        "implementation_sources": {
            "renderer": {
                "path": _manifest_path(Path(__file__).resolve()),
                "sha256": renderer_sha256,
            }
        },
        "source_inputs": {
            "core_run_manifest": {
                "path": _manifest_path(run_manifest_path),
                "sha256": run_manifest_sha256,
            },
            "station_timeline": {
                "path": _manifest_path(station_path),
                "sha256": station_sha256,
                "n_rows": station_row_count,
                "core_manifest_match": True,
            },
            "site_timeline": {
                "path": _manifest_path(site_path),
                "sha256": site_sha256,
                "n_rows": site_row_count,
                "core_manifest_match": True,
            },
        },
        "representative_days": representative_days,
        "plotted_subset_sha256": _plotted_subset_sha256(
            station_subset, selected
        ),
        "rendering": {
            "backend": str(matplotlib.get_backend()).lower(),
            "matplotlib_version": matplotlib.__version__,
            "python_version": sys.version.split()[0],
            "width_mm": 183,
            "height_mm": 135,
            "png_dpi": 300,
            "svg_text_editable": True,
            "palette": {
                "levels": LEVEL_COLORS,
                "delta_v": DELTA_V_COLORS,
                "not_site_confirmed": "#E7E7E7",
            },
        },
        "outputs": output_records,
        "not_claimed": [
            "formal_warning_output",
            "event_independent_warning_performance",
            "warning_lead_time",
            "supervised_calibration",
            "Vajont_external_case",
        ],
    }


def write_ootang_v3_typical_day_figure(
    *,
    station_timeline_path: Path,
    site_timeline_path: Path,
    run_manifest_path: Path,
    profile_path: Path = DEFAULT_PROFILE_PATH,
    figure_spec_path: Path = DEFAULT_FIGURE_SPEC_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> TypicalDayFigureArtifacts:
    """Validate, render, and atomically promote the v3 diagnostic figure."""

    station_path = Path(station_timeline_path).resolve()
    site_path = Path(site_timeline_path).resolve()
    core_manifest_path = Path(run_manifest_path).resolve()
    operational_profile_path = Path(profile_path).resolve()
    spec_path = Path(figure_spec_path).resolve()
    target_dir = Path(output_dir).resolve()

    profile, _profile_payload = _read_json_snapshot(
        operational_profile_path,
        label="operational profile",
    )
    figure_spec, figure_spec_payload = _read_json_snapshot(
        spec_path,
        label="figure specification",
    )
    core_manifest, core_manifest_payload = _read_json_snapshot(
        core_manifest_path,
        label="core run manifest",
    )
    station_rows, station_payload = _read_csv_snapshot(
        station_path,
        label="station timeline",
    )
    site_rows, site_payload = _read_csv_snapshot(
        site_path,
        label="site timeline",
    )
    renderer_payload = Path(__file__).resolve().read_bytes()
    if (
        figure_spec.get("formal_warning_output") is not False
        or figure_spec.get("vajont_used") is not False
        or figure_spec.get("case") != "ootang"
    ):
        raise TypicalDayFigureInputError(
            "Figure specification must remain non-formal and Ootang-only."
        )
    rules = figure_spec.get("representative_day_rules")
    if not isinstance(rules, list):
        raise TypicalDayFigureInputError(
            "Figure specification lacks representative-day rules."
        )
    _validate_run_provenance(
        manifest=core_manifest,
        station_sha256=_sha256_bytes(station_payload),
        site_sha256=_sha256_bytes(site_payload),
        profile=profile,
    )
    _require_columns(station_rows, STATION_REQUIRED_COLUMNS, label="station timeline")
    _require_columns(site_rows, SITE_REQUIRED_COLUMNS, label="site timeline")
    if site_rows["formal_warning_output"].astype(bool).any():
        raise TypicalDayFigureInputError("Site rows unexpectedly claim formal output.")
    if site_rows["vajont_used"].astype(bool).any():
        raise TypicalDayFigureInputError("Site rows unexpectedly use Vajont.")

    selected = select_representative_days(site_rows, rules)
    _validate_frozen_expectations(selected, rules)
    stations, station_blocks = _station_layout(profile)
    station_subset = _selected_station_rows(
        station_rows,
        selected["date"].astype(str).tolist(),
        stations,
    )

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    targets = (
        target_dir / f"{FIGURE_STEM}.svg",
        target_dir / f"{FIGURE_STEM}.pdf",
        target_dir / f"{FIGURE_STEM}.png",
    )
    manifest_target = target_dir / MANIFEST_FILENAME
    with tempfile.TemporaryDirectory(
        prefix=f".{target_dir.name}.typical-days-",
        dir=target_dir.parent,
    ) as directory:
        staging_dir = Path(directory)
        with plt.rc_context(FIGURE_RC_PARAMS):
            existing_figures = set(plt.get_fignums())
            try:
                figure = _render_figure(
                    station_rows=station_subset,
                    selected=selected,
                    stations=stations,
                    station_blocks=station_blocks,
                )
            except BaseException:
                for figure_number in set(plt.get_fignums()).difference(
                    existing_figures
                ):
                    plt.close(figure_number)
                raise
            try:
                staged_outputs = _save_figure(figure, staging_dir)
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
                    selected=selected,
                    station_subset=station_subset,
                    figure_spec_sha256=_sha256_bytes(figure_spec_payload),
                    run_manifest_sha256=_sha256_bytes(core_manifest_payload),
                    station_sha256=_sha256_bytes(station_payload),
                    site_sha256=_sha256_bytes(site_payload),
                    station_row_count=len(station_rows),
                    site_row_count=len(site_rows),
                    renderer_sha256=_sha256_bytes(renderer_payload),
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

    return TypicalDayFigureArtifacts(
        svg_path=targets[0],
        pdf_path=targets[1],
        png_path=targets[2],
        manifest_path=manifest_target,
    )


__all__ = [
    "DEFAULT_FIGURE_SPEC_PATH",
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_PROFILE_PATH",
    "REPRESENTATIVE_DAY_RULE_IDS",
    "TypicalDayFigureArtifacts",
    "TypicalDayFigureInputError",
    "select_representative_days",
    "write_ootang_v3_typical_day_figure",
]
