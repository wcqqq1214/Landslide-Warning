"""Shared, public support for traceable Ootang v3 figure renderers."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pandas as pd
from matplotlib.figure import Figure

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_PROFILE_ID = "ootang-operational-spatial-v3"
EXPECTED_PROFILE_VERSION = "3.0-draft"
ARTIFACT_STATUS = "operational_draft_not_formal"

LEVEL_NAMES = ("green", "blue", "yellow", "orange", "red")
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

BASE_FIGURE_RC_PARAMS = {
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "DejaVu Sans", "Liberation Sans"],
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "font.size": 6,
    "axes.linewidth": 0.7,
    "axes.spines.right": False,
    "axes.spines.top": False,
    "legend.frameon": False,
}


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 digest for one file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    """Return the SHA-256 digest of an in-memory snapshot."""

    return hashlib.sha256(payload).hexdigest()


def manifest_path(path: Path) -> str:
    """Prefer repository-relative paths while preserving external paths."""

    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def canonical_json_sha256(value: Any) -> str:
    """Hash a JSON-compatible value using one deterministic encoding."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_json_snapshot(
    path: Path,
    *,
    label: str,
    error_type: type[ValueError],
) -> tuple[dict[str, Any], bytes]:
    """Read one JSON object and retain the exact bytes used for provenance."""

    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise error_type(f"Cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise error_type(f"{label} must contain a JSON object.")
    return value, payload


def read_csv_snapshot(
    path: Path,
    *,
    label: str,
    error_type: type[ValueError],
) -> tuple[pd.DataFrame, bytes]:
    """Read one CSV frame and retain the exact bytes used for provenance."""

    try:
        payload = path.read_bytes()
        frame = pd.read_csv(io.BytesIO(payload), dtype={"date": str})
    except (OSError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise error_type(f"Cannot read {label}: {path}") from exc
    return frame, payload


def require_columns(
    frame: pd.DataFrame,
    required: set[str],
    *,
    label: str,
    error_type: type[ValueError],
) -> None:
    """Require the declared columns using the caller's public error type."""

    missing = sorted(required.difference(frame.columns))
    if missing:
        raise error_type(f"{label} is missing required columns: {', '.join(missing)}")


def validate_run_provenance(
    *,
    manifest: dict[str, Any],
    station_sha256: str,
    site_sha256: str,
    profile: dict[str, Any],
    error_type: type[ValueError],
) -> None:
    """Validate that figure inputs belong to the frozen, non-formal v3 run."""

    if manifest.get("formal_warning_output") is not False:
        raise error_type("Core run must remain non-formal.")
    if manifest.get("vajont_used") is not False:
        raise error_type("Core run unexpectedly used Vajont.")
    operational_profile = manifest.get("operational_profile")
    if not isinstance(operational_profile, dict):
        raise error_type("Core run lacks an operational profile.")
    profile_sha = canonical_json_sha256(profile)
    if (
        operational_profile.get("id") != EXPECTED_PROFILE_ID
        or operational_profile.get("version") != EXPECTED_PROFILE_VERSION
        or operational_profile.get("content_sha256") != profile_sha
    ):
        raise error_type("Core run does not match the frozen Ootang v3 profile.")

    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise error_type("Core run lacks output provenance.")
    for key, source_sha256 in (
        ("station_timeline", station_sha256),
        ("site_timeline", site_sha256),
    ):
        record = outputs.get(key)
        if not isinstance(record, dict) or record.get("sha256") != source_sha256:
            raise error_type(f"{key} does not match the core run manifest.")

    sources = manifest.get("implementation_sources")
    if not isinstance(sources, dict) or not sources:
        raise error_type("Core run lacks implementation-source fingerprints.")
    for name, source in sources.items():
        if not isinstance(source, dict) or not isinstance(source.get("path"), str):
            raise error_type(f"Invalid implementation-source record: {name}.")
        source_path = Path(source["path"])
        if not source_path.is_absolute():
            source_path = ROOT / source_path
        if not source_path.is_file() or source.get("sha256") != sha256_file(
            source_path
        ):
            raise error_type(f"Core run implementation fingerprint is stale: {name}.")


def station_layout(
    profile: dict[str, Any],
    *,
    error_type: type[ValueError],
) -> tuple[list[str], dict[str, str]]:
    """Return the frozen ordered O1/O2/O3 station layout."""

    site_fusion = profile.get("site_fusion")
    if not isinstance(site_fusion, dict):
        raise error_type("Profile lacks site_fusion.")
    blocks = site_fusion.get("spatial_blocks")
    if not isinstance(blocks, dict) or tuple(blocks) != ("O1", "O2", "O3"):
        raise error_type("Profile must declare ordered O1/O2/O3 spatial blocks.")
    stations: list[str] = []
    station_blocks: dict[str, str] = {}
    for block, members in blocks.items():
        if not isinstance(members, list):
            raise error_type(f"Spatial block {block} is invalid.")
        for station in members:
            name = str(station)
            stations.append(name)
            station_blocks[name] = str(block)
    if len(stations) != len(set(stations)) or len(stations) != 8:
        raise error_type("Ootang v3 figures require eight unique stations.")
    return stations, station_blocks


def export_figure_bundle(
    figure: Figure,
    directory: Path,
    *,
    figure_stem: str,
    error_type: type[ValueError],
) -> tuple[Path, Path, Path]:
    """Export and validate deterministic SVG, PDF, and PNG figure files."""

    svg_path = directory / f"{figure_stem}.svg"
    pdf_path = directory / f"{figure_stem}.pdf"
    png_path = directory / f"{figure_stem}.png"
    figure.savefig(
        svg_path,
        format="svg",
        metadata={"Date": None, "Creator": "Landslide-Warning"},
    )
    svg_text = svg_path.read_text(encoding="utf-8")
    svg_path.write_text(
        "\n".join(line.rstrip() for line in svg_text.splitlines()) + "\n",
        encoding="utf-8",
    )
    figure.savefig(
        pdf_path,
        format="pdf",
        metadata={
            "CreationDate": None,
            "ModDate": None,
            "Creator": "Landslide-Warning",
        },
    )
    figure.savefig(
        png_path,
        format="png",
        dpi=300,
        metadata={"Software": "Landslide-Warning"},
    )
    if not svg_path.read_bytes().lstrip().startswith(b"<?xml"):
        raise error_type("SVG export is invalid.")
    if not pdf_path.read_bytes().startswith(b"%PDF-"):
        raise error_type("PDF export is invalid.")
    if not png_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
        raise error_type("PNG export is invalid.")
    return svg_path, pdf_path, png_path


__all__ = [
    "ARTIFACT_STATUS",
    "BASE_FIGURE_RC_PARAMS",
    "DELTA_V_COLORS",
    "DELTA_V_SYMBOLS",
    "LEVEL_ABBREVIATIONS",
    "LEVEL_COLORS",
    "LEVEL_NAMES",
    "canonical_json_sha256",
    "export_figure_bundle",
    "manifest_path",
    "read_csv_snapshot",
    "read_json_snapshot",
    "require_columns",
    "sha256_bytes",
    "sha256_file",
    "station_layout",
    "validate_run_provenance",
]
