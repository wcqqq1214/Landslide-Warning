"""Compact, version-owned figures for the non-formal Ootang v4 run.

The v3 renderers deliberately describe a three-state ``delta_v`` strip.  This
module keeps v3 untouched and renders the v4 four-indicator contract explicitly:
interval, velocity/tangent, acceleration, and the fused candidate.  The raw
``delta_v`` state remains available in the CSV but is not drawn as a warning
level.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import ListedColormap

from warning import operational_v3_figure_support as support
from warning.protocol import load_protocol, protocol_content_sha256

ROOT = Path(__file__).resolve().parents[2]
LEVEL_NAMES = support.LEVEL_NAMES
LEVEL_COLORS = support.LEVEL_COLORS
LEVEL_CMAP = ListedColormap([LEVEL_COLORS[name] for name in LEVEL_NAMES])
STATIONS = ("MJ9", "MJ1", "MJ3", "ATU4", "ATU5", "ATU3", "ATU2", "ATU1")
STRIP_FIELDS = (
    "interval_level",
    "velocity_level",
    "acceleration_level",
    "tangent_angle_level",
    "candidate_level",
)
STRIP_LABELS = ("I", "V", "A", "T", "F")
ARTIFACT_STATUS = "operational_draft_not_formal"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_inputs(
    station_path: Path,
    site_path: Path,
    manifest_path: Path,
    profile_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], dict[str, Any]]:
    station = pd.read_csv(station_path)
    site = pd.read_csv(site_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    required_station = {
        "date", "station", "split", "actual", *STRIP_FIELDS,
        "formal_warning_output", "vajont_used",
    }
    required_site = {
        "date", "split", "site_confirmed_level", "local_max_candidate_level",
        "site_fusion_status", "formal_warning_output", "vajont_used",
    }
    missing_station = sorted(required_station.difference(station.columns))
    missing_site = sorted(required_site.difference(site.columns))
    if missing_station or missing_site:
        raise ValueError(
            f"v4 figure inputs missing station={missing_station}, site={missing_site}"
        )
    if manifest.get("formal_warning_output") is not False or manifest.get("vajont_used") is not False:
        raise ValueError("v4 figure source must remain non-formal Ootang-only")
    if manifest.get("operational_profile", {}).get("id") != "ootang-operational-spatial-v4":
        raise ValueError("v4 figure source does not match the v4 profile")
    if profile.get("profile_id") != "ootang-operational-spatial-v4":
        raise ValueError("v4 figure profile id is invalid")

    # Figure bundles are downstream artifacts, so refuse to render against a
    # stale core run.  This keeps the source/output hashes, row counts, shared
    # implementation fingerprints and both protocol fingerprints auditable in
    # every figure manifest rather than trusting only the caller's paths.
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("v4 core manifest lacks output provenance")
    for key, path in (
        ("station_timeline", station_path),
        ("site_timeline", site_path),
    ):
        declaration = outputs.get(key)
        if not isinstance(declaration, dict):
            raise ValueError(f"v4 core manifest lacks {key} provenance")
        if declaration.get("sha256") != _sha256(path):
            raise ValueError(f"v4 core {key} hash is stale")
        declared_rows = declaration.get("n_rows")
        if declared_rows is not None and declared_rows != len(
            station if key == "station_timeline" else site
        ):
            raise ValueError(f"v4 core {key} row count is stale")
    thresholds = outputs.get("thresholds")
    if not isinstance(thresholds, dict) or not isinstance(thresholds.get("n_rows"), int):
        raise ValueError("v4 core manifest lacks threshold row count")
    if thresholds["n_rows"] != len(profile.get("ootang_stations", ())):
        raise ValueError("v4 core threshold row count is stale")

    protocol = manifest.get("base_draft_protocol")
    extension = manifest.get("acceleration_protocol_extension")
    if not isinstance(protocol, dict) or not isinstance(extension, dict):
        raise ValueError("v4 core manifest must bind both protocol hashes")
    for name, declaration in (("base protocol", protocol), ("acceleration protocol", extension)):
        path = ROOT / str(declaration.get("path", ""))
        if not path.is_file():
            raise ValueError(f"v4 {name} path is missing")
        if declaration.get("content_sha256") != protocol_content_sha256(load_protocol(path)):
            raise ValueError(f"v4 {name} hash is stale")
    implementation_sources = manifest.get("implementation_sources")
    if not isinstance(implementation_sources, dict) or not implementation_sources:
        raise ValueError("v4 core manifest lacks implementation sources")
    for name, declaration in implementation_sources.items():
        if not isinstance(declaration, dict):
            raise ValueError(f"v4 implementation source {name} is malformed")
        path = ROOT / str(declaration.get("path", ""))
        if not path.is_file() or declaration.get("sha256") != _sha256(path):
            raise ValueError(f"v4 implementation source {name} hash is stale")
    station["date"] = station["date"].astype(str)
    site["date"] = site["date"].astype(str)
    if set(station["station"].astype(str)) != set(STATIONS):
        raise ValueError("v4 station figure input does not contain the eight stations")
    if station[["date", "station"]].duplicated().any():
        raise ValueError("v4 station timeline has duplicate station/date rows")
    if not station["formal_warning_output"].eq(False).all() or not station["vajont_used"].eq(False).all():
        raise ValueError("v4 station timeline has invalid provenance flags")
    if not site["formal_warning_output"].eq(False).all() or not site["vajont_used"].eq(False).all():
        raise ValueError("v4 site timeline has invalid provenance flags")
    for field in STRIP_FIELDS:
        values = pd.to_numeric(station[field], errors="coerce")
        if values.isna().any() or not values.between(0, 4).all():
            raise ValueError(f"v4 station timeline contains invalid {field}")
        station[field] = values.astype(int)
    station = station.sort_values(["date", "station"], kind="stable").reset_index(drop=True)
    site = site.sort_values("date", kind="stable").reset_index(drop=True)
    return station, site, manifest, profile


def _export(fig: plt.Figure, output_dir: Path, stem: str) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "svg": output_dir / f"{stem}.svg",
        "pdf": output_dir / f"{stem}.pdf",
        "png": output_dir / f"{stem}.png",
    }
    fig.savefig(paths["svg"], metadata={"Date": None, "Creator": "Landslide-Warning"})
    svg = paths["svg"].read_text(encoding="utf-8")
    paths["svg"].write_text("\n".join(line.rstrip() for line in svg.splitlines()) + "\n", encoding="utf-8")
    fig.savefig(paths["pdf"], metadata={"CreationDate": None, "ModDate": None, "Creator": "Landslide-Warning"})
    fig.savefig(paths["png"], dpi=300, metadata={"Software": "Landslide-Warning"})
    plt.close(fig)
    return paths


def _write_manifest(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _common_manifest(
    *,
    stem: str,
    station_path: Path,
    site_path: Path,
    core_manifest_path: Path,
    profile_path: Path,
    outputs: dict[str, Path],
    extra: dict[str, Any],
) -> dict[str, Any]:
    core_manifest = json.loads(core_manifest_path.read_text(encoding="utf-8"))
    core_outputs = core_manifest.get("outputs", {})
    core_output_row_counts = {
        name: declaration.get("n_rows")
        for name, declaration in core_outputs.items()
        if isinstance(declaration, dict) and isinstance(declaration.get("n_rows"), int)
    }
    return {
        "artifact_kind": f"ootang_operational_v4_{stem}",
        "artifact_status": ARTIFACT_STATUS,
        "formal_warning_output": False,
        "vajont_used": False,
        "case": "ootang",
        "profile": {
            "id": "ootang-operational-spatial-v4",
            "path": support.manifest_path(profile_path),
            "sha256": _sha256(profile_path),
        },
        "core_run_manifest": {
            "path": support.manifest_path(core_manifest_path),
            "sha256": _sha256(core_manifest_path),
        },
        # Carry the core run's protocol and implementation declarations into
        # each figure sidecar.  A figure is therefore independently auditable
        # without following an unverified chain of manifests.
        "base_draft_protocol": core_manifest.get("base_draft_protocol"),
        "acceleration_protocol_extension": core_manifest.get(
            "acceleration_protocol_extension"
        ),
        "implementation_sources": core_manifest.get("implementation_sources"),
        "core_output_row_counts": core_output_row_counts,
        "source_inputs": {
            "station_timeline": {
                "path": support.manifest_path(station_path),
                "sha256": _sha256(station_path),
                "n_rows": core_output_row_counts.get("station_timeline"),
            },
            "site_timeline": {
                "path": support.manifest_path(site_path),
                "sha256": _sha256(site_path),
                "n_rows": core_output_row_counts.get("site_timeline"),
            },
        },
        "outputs": {
            suffix: {"path": support.manifest_path(output), "sha256": _sha256(output)}
            for suffix, output in outputs.items()
        },
        **extra,
    }


def write_v4_station_combined_diagnostic(
    *, station_path: Path, site_path: Path, core_manifest_path: Path,
    profile_path: Path, output_dir: Path,
) -> Path:
    station, site, _, _ = _read_inputs(station_path, site_path, core_manifest_path, profile_path)
    dates = sorted(station["date"].unique())
    fig, axes = plt.subplots(8, 2, figsize=(7.2, 10.0), sharex="col", constrained_layout=True)
    for row_index, station_name in enumerate(STATIONS):
        rows = station.loc[station["station"].eq(station_name)].sort_values("date")
        x = np.arange(len(rows))
        line_ax, strip_ax = axes[row_index]
        line_ax.plot(x, rows["actual"], color="#303030", linewidth=0.55)
        line_ax.set_ylabel(f"{station_name}\nmm", fontsize=5)
        line_ax.grid(axis="y", color="#dddddd", linewidth=0.3)
        matrix = rows.loc[:, STRIP_FIELDS].to_numpy(dtype=float).T
        strip_ax.imshow(matrix, aspect="auto", interpolation="nearest", cmap=LEVEL_CMAP, vmin=-0.5, vmax=4.5)
        strip_ax.set_yticks(range(len(STRIP_LABELS)), STRIP_LABELS, fontsize=5)
        strip_ax.tick_params(axis="y", length=0)
        strip_ax.set_xticks([])
        if row_index == 0:
            line_ax.set_title("Observed displacement", fontsize=7)
            strip_ax.set_title("I interval · V velocity · A acceleration · T tangent · F fused", fontsize=7)
    axes[-1, 0].set_xlabel("result date index")
    axes[-1, 1].set_xlabel(f"{len(dates)} dates; acceleration is an independent v4 family")
    fig.suptitle("Ootang v4 all-station diagnostic (observed-after-forecast; non-formal)", fontsize=9)
    outputs = _export(fig, output_dir, "ootang_v4_all_station_combined_diagnostic")
    manifest_path = output_dir / "ootang_v4_all_station_combined_diagnostic_manifest.json"
    manifest = _common_manifest(
        stem="all_station_combined_diagnostic", station_path=station_path, site_path=site_path,
        core_manifest_path=core_manifest_path, profile_path=profile_path, outputs=outputs,
        extra={
            "timing_semantics": "target_observation_available_before_state_assignment",
            "timeline_coverage": {"date_count": len(dates), "station_count": len(STATIONS), "station_row_count": len(station)},
            "field_mappings": {"interval": "interval_level", "velocity": "velocity_level", "acceleration": "acceleration_level", "tangent": "tangent_angle_level", "fused": "candidate_level"},
            "strip_order": list(STRIP_LABELS),
            "acceleration_state_counts": {str(k): int(v) for k, v in station["acceleration_level"].value_counts().sort_index().items()},
        },
    )
    return _write_manifest(manifest_path, manifest)


def write_v4_full_timeline(
    *, station_path: Path, site_path: Path, core_manifest_path: Path,
    profile_path: Path, output_dir: Path,
) -> Path:
    station, site, _, _ = _read_inputs(station_path, site_path, core_manifest_path, profile_path)
    dates = sorted(site["date"].unique())
    station_matrix = station.pivot(index="station", columns="date", values="candidate_level").reindex(STATIONS)[dates]
    site_confirmed = pd.to_numeric(site.set_index("date").reindex(dates)["site_confirmed_level"], errors="coerce")
    local = pd.to_numeric(site.set_index("date").reindex(dates)["local_max_candidate_level"], errors="coerce")
    fig, axes = plt.subplots(3, 1, figsize=(11.0, 5.5), sharex=True, constrained_layout=True, gridspec_kw={"height_ratios": [4, 1, 1]})
    axes[0].imshow(station_matrix.to_numpy(dtype=float), aspect="auto", interpolation="nearest", cmap=LEVEL_CMAP, vmin=-0.5, vmax=4.5)
    axes[0].set_yticks(range(len(STATIONS)), STATIONS, fontsize=6)
    axes[0].set_ylabel("station candidate", fontsize=7)
    axes[1].plot(np.arange(len(dates)), local.to_numpy(dtype=float), color="#C44E52", linewidth=0.7, drawstyle="steps-mid")
    axes[1].set_ylim(-0.5, 4.5)
    axes[1].set_yticks(range(5), ["G", "B", "Y", "O", "R"], fontsize=6)
    axes[1].set_ylabel("local max", fontsize=7)
    axes[2].plot(np.arange(len(dates)), site_confirmed.to_numpy(dtype=float), color="#4C78A8", linewidth=0.7, drawstyle="steps-mid")
    axes[2].set_ylim(-0.5, 4.5)
    axes[2].set_yticks(range(5), ["G", "B", "Y", "O", "R"], fontsize=6)
    axes[2].set_ylabel("site", fontsize=7)
    axes[2].set_xlabel("result date index")
    for ax in axes:
        ax.grid(axis="y", color="#dddddd", linewidth=0.3)
    fig.suptitle("Ootang v4 complete warning-state timeline (non-formal; site and local axes)", fontsize=9)
    outputs = _export(fig, output_dir, "ootang_v4_full_warning_timeline")
    manifest_path = output_dir / "ootang_v4_full_warning_timeline_manifest.json"
    return _write_manifest(
        manifest_path,
        _common_manifest(
            stem="full_warning_timeline", station_path=station_path, site_path=site_path,
            core_manifest_path=core_manifest_path, profile_path=profile_path, outputs=outputs,
            extra={"timeline_coverage": {"date_count": len(dates), "station_count": len(STATIONS), "station_row_count": len(station)}, "axes": ["station_candidate", "local_max_candidate", "site_confirmed"]},
        ),
    )


def write_v4_typical_days(
    *, station_path: Path, site_path: Path, core_manifest_path: Path,
    profile_path: Path, output_dir: Path,
) -> Path:
    station, site, _, _ = _read_inputs(station_path, site_path, core_manifest_path, profile_path)
    ordered = site.sort_values("date")
    chosen: list[str] = []
    for color in ("green", "blue", "yellow", "orange", "red"):
        candidates = ordered.loc[ordered["site_confirmed_level"].map(lambda x: LEVEL_NAMES[int(x)] if pd.notna(x) else None).eq(color), "date"]
        if not candidates.empty:
            chosen.append(str(candidates.iloc[0]))
    if len(chosen) < 3:
        chosen = list(ordered["date"].head(min(5, len(ordered))))
    chosen = list(dict.fromkeys(chosen))
    subset = station.loc[station["date"].isin(chosen)].copy()
    fig, axes = plt.subplots(1, len(chosen), figsize=(2.4 * len(chosen), 4.2), squeeze=False, constrained_layout=True)
    for index, date in enumerate(chosen):
        matrix = subset.loc[subset["date"].eq(date)].set_index("station").reindex(STATIONS).loc[:, STRIP_FIELDS]
        ax = axes[0, index]
        ax.imshow(matrix.to_numpy(dtype=float), aspect="auto", interpolation="nearest", cmap=LEVEL_CMAP, vmin=-0.5, vmax=4.5)
        ax.set_title(date, fontsize=6, rotation=45, ha="left")
        ax.set_xticks(range(len(STRIP_LABELS)), STRIP_LABELS, fontsize=6)
        ax.set_yticks(range(len(STATIONS)), STATIONS if index == 0 else [], fontsize=5)
    fig.suptitle("Ootang v4 representative dates: interval / velocity / acceleration / tangent / fused", fontsize=8)
    outputs = _export(fig, output_dir, "ootang_v4_typical_days")
    manifest_path = output_dir / "ootang_v4_typical_days_manifest.json"
    return _write_manifest(
        manifest_path,
        _common_manifest(
            stem="typical_days", station_path=station_path, site_path=site_path,
            core_manifest_path=core_manifest_path, profile_path=profile_path, outputs=outputs,
            extra={"selected_dates": chosen, "strip_order": list(STRIP_LABELS), "selection_policy": "earliest_available_site_confirmed_colors_then_fallback"},
        ),
    )


__all__ = ["write_v4_station_combined_diagnostic", "write_v4_full_timeline", "write_v4_typical_days"]
