"""Automatic fit-only V0 candidates from raw displacement change points.

This is an exploratory candidate diagnostic. It does not modify v4, emit warning
levels, or promote a candidate to a formal V0.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.bai_perron_initial_slope import (  # noqa: E402
    BIC_FORMULA,
    MAX_SEGMENTS,
    MIN_SEGMENT_OBSERVATIONS,
    REGRESSION_PARAMETERS_PER_SEGMENT,
    SEGMENTATION_ALGORITHM,
    _select_bic_segmented_model,
)
from warning.draft_evidence import FileReplacement, OOTANG_STATIONS, promote_staged_files  # noqa: E402
from warning import ootang_ngboost_interval_proxy_pilot as base  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = ROOT / "config" / "ootang_auto_v0_direct_bai_perron.v1.json"
DEFAULT_KINEMATICS_PATH = ROOT / "data" / "ootang_kinematics_long.csv"
DEFAULT_PREDICTIONS_PATH = ROOT / "figures" / "convlstm" / "forecast_predictions.csv"
DEFAULT_FORECAST_MANIFEST_PATH = ROOT / "figures" / "convlstm" / "forecast_run_manifest.json"
DEFAULT_THRESHOLDS_PATH = ROOT / "figures" / "warning_operational_draft_v4" / "ootang_operational_thresholds.csv"
DEFAULT_V4_MANIFEST_PATH = ROOT / "figures" / "warning_operational_draft_v4" / "ootang_operational_run_manifest.json"
DEFAULT_OUTPUT_DIR = ROOT / "figures" / "auto_v0_direct_bai_perron_ootang_v1"
ARTIFACT_KIND = "ootang_auto_v0_direct_bai_perron_candidate"
ARTIFACT_STATUS = "exploratory_v0_candidate_not_formal"
CANDIDATE_COLUMNS = (
    "case",
    "station",
    "fit_start_date",
    "fit_end_date",
    "fit_rows",
    "selection_status",
    "failure_reason",
    "selected_segment_count",
    "selected_bic",
    "one_segment_bic",
    "selected_segment_start_date",
    "selected_segment_end_date",
    "selected_segment_n",
    "first_break_date",
    "following_segment_slope_mm_per_day",
    "candidate_v_mm_per_day",
    "selected_velocity_n",
    "selected_velocity_mean_mm_per_day",
    "selected_velocity_sigma_mm_per_day",
    "candidate_v0_mm_per_day",
    "candidate_v0_status",
    "formal_warning_output",
    "candidate_v0_only",
    "vajont_used",
)
SEGMENT_COLUMNS = (
    "case",
    "station",
    "fit_start_date",
    "fit_end_date",
    "segment_index",
    "segment_start_date",
    "segment_end_date",
    "segment_n",
    "intercept",
    "slope_mm_per_day",
    "sse",
    "selected_segment",
    "following_segment",
    "formal_warning_output",
    "candidate_v0_only",
    "vajont_used",
)


class AutoV0ProfileError(ValueError):
    """Raised when the automatic V0 profile drifts."""


class AutoV0InputError(RuntimeError):
    """Raised when an automatic V0 source violates the contract."""


class AutoV0OutputError(RuntimeError):
    """Raised when an automatic V0 output violates its contract."""


def _sha256_file(path: Path) -> str:
    digest = __import__("hashlib").sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _canonical_json_sha256(payload: dict[str, Any]) -> str:
    return __import__("hashlib").sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _require_equal(actual: object, expected: object, *, name: str) -> None:
    if actual != expected:
        raise AutoV0ProfileError(f"{name} must be {expected!r}; found {actual!r}")


def load_auto_v0_profile(path: Path = DEFAULT_PROFILE_PATH) -> dict[str, Any]:
    try:
        profile = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AutoV0ProfileError(f"Missing profile: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise AutoV0ProfileError(f"Invalid profile: {path}") from exc
    if not isinstance(profile, dict):
        raise AutoV0ProfileError("Profile must be an object")
    fixed = {
        "profile_id": "ootang-auto-v0-direct-bai-perron-v1",
        "profile_version": "1.0-candidate",
        "status": "exploratory_v0_candidate",
        "case": "ootang",
        "formal_warning_output": False,
        "candidate_v0_only": True,
        "vajont_used": False,
        "default_pipeline_member": False,
        "stations": list(OOTANG_STATIONS),
    }
    for name, expected in fixed.items():
        _require_equal(profile.get(name), expected, name=name)
    input_contract = profile.get("input")
    expected_input = {
        "displacement_source": "data/ootang_kinematics_long.csv",
        "displacement_columns": ["case", "date", "station", "displacement", "displacement_valid"],
        "velocity_source": "data/ootang_kinematics_long.csv",
        "velocity_columns": ["date", "station", "velocity", "velocity_status"],
        "fit_boundary_source": "figures/convlstm/forecast_predictions.csv",
        "fit_only": True,
        "time_axis": "actual_elapsed_days",
        "no_manual_stage_ranges": True,
        "no_calibration_or_test_selection": True,
    }
    _require_equal(input_contract, expected_input, name="input")
    segmentation = profile.get("segmentation")
    expected_segmentation = {
        "algorithm": SEGMENTATION_ALGORITHM,
        "segment_count_selection": "bic",
        "minimum_segment_observations": MIN_SEGMENT_OBSERVATIONS,
        "max_segments": MAX_SEGMENTS,
        "regression_parameters_per_segment": REGRESSION_PARAMETERS_PER_SEGMENT,
        "continuity_constraint": "none",
        "bic_formula": BIC_FORMULA.replace("trend_displacement", "displacement"),
        "initial_segment_rule": "positive_first_slope_and_immediately_following_slope_greater",
        "single_segment_rule": "positive_full_fit_is_stable_full_fit_baseline",
        "failure_policy": "unavailable_with_reason",
        "mvif_prerequisite": False,
        "kmeans_fallback": False,
    }
    _require_equal(segmentation, expected_segmentation, name="segmentation")
    v0 = profile.get("v0")
    expected_v0 = {
        "V_formula": "OLS_slope_of_selected_raw_cumulative_displacement_segment_mm_per_day",
        "sigma_formula": "sample_std_ddof1_of_raw_point_velocities_inside_selected_segment_mm_per_day",
        "V0_formula": "max(1.5*V,V+2*sigma_V)",
        "positive_finite_required": True,
        "status_values": ["initial_segment_selected", "stable_full_fit_baseline", "unavailable"],
        "formal_v0": False,
    }
    _require_equal(v0, expected_v0, name="v0")
    outputs = profile.get("outputs")
    expected_outputs = {
        "directory": "figures/auto_v0_direct_bai_perron_ootang_v1",
        "candidates": "candidates.csv",
        "segments": "segments.csv",
        "figure_png": "candidate_diagnostics.png",
        "figure_svg": "candidate_diagnostics.svg",
        "manifest": "manifest.json",
    }
    _require_equal(outputs, expected_outputs, name="outputs")
    not_claimed = profile.get("not_claimed")
    if not isinstance(not_claimed, list) or "formal_v0" not in not_claimed:
        raise AutoV0ProfileError("not_claimed must include formal_v0")
    return profile


def _validate_output_dir(output_dir: Path) -> None:
    resolved = output_dir.resolve()
    if resolved.is_relative_to(ROOT.resolve()) and resolved != DEFAULT_OUTPUT_DIR.resolve():
        raise AutoV0OutputError("Repository output_dir is fixed to the automatic V0 namespace")
    if resolved.exists() and resolved.is_file():
        raise AutoV0OutputError("Automatic V0 output_dir must be a directory")


def segment_piecewise_linear_signal(
    signal: pd.DataFrame,
    *,
    date_column: str = "date",
    value_column: str = "displacement",
) -> dict[str, Any]:
    """Segment a fit-only raw signal with the existing Bai-Perron BIC engine."""
    if not isinstance(signal, pd.DataFrame):
        raise ValueError("signal must be a DataFrame")
    missing = {date_column, value_column}.difference(signal.columns)
    if missing:
        raise ValueError(f"signal is missing required columns: {sorted(missing)}")
    frame = signal.loc[:, [date_column, value_column]].copy()
    frame.columns = ["date", "value"]
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    if frame.empty:
        raise ValueError("signal must not be empty")
    if frame["date"].isna().any() or frame["value"].isna().any():
        raise ValueError("signal contains invalid dates or values")
    if frame["date"].duplicated().any() or not frame["date"].is_monotonic_increasing:
        raise ValueError("signal dates must be strictly increasing and unique")
    if len(frame) < MIN_SEGMENT_OBSERVATIONS:
        raise ValueError("signal has fewer than the minimum segment observations")

    time_days = (
        frame["date"]
        .sub(frame["date"].iloc[0])
        .dt.total_seconds()
        .to_numpy(dtype=float)
        / 86_400.0
    )
    values = frame["value"].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("signal contains nonfinite values")
    value_origin = float(values[0])
    models, selected_count, selected_bic, one_segment_bic = (
        _select_bic_segmented_model(
            time_days=time_days,
            trend_displacement=values - value_origin,
        )
    )
    segments = []
    for segment in models:
        segments.append(
            {
                "start_index": segment.start_index,
                "end_index": segment.end_index,
                "start_date": pd.Timestamp(
                    frame["date"].iloc[segment.start_index]
                ),
                "end_date": pd.Timestamp(
                    frame["date"].iloc[segment.end_index - 1]
                ),
                "intercept": segment.intercept + value_origin,
                "slope_mm_per_day": segment.slope,
                "sse": segment.sse,
                "n_observations": segment.end_index - segment.start_index,
            }
        )
    return {
        "segments": segments,
        "selected_segment_count": selected_count,
        "selected_bic": selected_bic,
        "one_segment_bic": one_segment_bic,
        "min_segment_observations": MIN_SEGMENT_OBSERVATIONS,
        "max_segments_considered": min(
            MAX_SEGMENTS, len(frame) // MIN_SEGMENT_OBSERVATIONS
        ),
        "bic_formula": BIC_FORMULA,
        "algorithm": SEGMENTATION_ALGORITHM,
    }


def _load_fit_end_dates(predictions_path: Path) -> dict[str, pd.Timestamp]:
    predictions = pd.read_csv(predictions_path, usecols=["date", "station", "split"])
    predictions["date"] = pd.to_datetime(predictions["date"], errors="coerce")
    predictions["station"] = predictions["station"].astype("string").str.strip()
    if predictions["date"].isna().any() or predictions["station"].isna().any():
        raise AutoV0InputError("Forecast boundaries contain invalid date/station")
    if set(predictions["station"].tolist()) != set(OOTANG_STATIONS):
        raise AutoV0InputError("Forecast boundaries must contain exactly Ootang stations")
    if predictions.duplicated(["date", "station"]).any():
        raise AutoV0InputError("Forecast boundaries contain duplicate station/date rows")
    result = {}
    for station, group in predictions.groupby("station", sort=False):
        fit_dates = group.loc[group["split"].eq("fit"), "date"]
        if fit_dates.empty:
            raise AutoV0InputError(f"Station {station} has no fit boundary")
        result[str(station)] = pd.Timestamp(fit_dates.max())
    return result


def _validate_kinematics(kinematics: pd.DataFrame) -> pd.DataFrame:
    required = {
        "case", "date", "station", "displacement", "displacement_valid",
        "velocity", "velocity_status",
    }
    missing = required.difference(kinematics.columns)
    if missing:
        raise AutoV0InputError(f"Kinematics missing columns: {sorted(missing)}")
    frame = kinematics.loc[:, sorted(required)].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["station"] = frame["station"].astype("string").str.strip()
    frame["case"] = frame["case"].astype("string").str.strip()
    frame["displacement"] = pd.to_numeric(frame["displacement"], errors="coerce")
    frame["velocity"] = pd.to_numeric(frame["velocity"], errors="coerce")
    if not frame["case"].eq("ootang").all():
        raise AutoV0InputError("Automatic V0 accepts case=ootang only")
    if frame["date"].isna().any() or frame["displacement"].isna().any():
        raise AutoV0InputError("Kinematics contains invalid fit inputs")
    if set(frame["station"].tolist()) != set(OOTANG_STATIONS):
        raise AutoV0InputError("Kinematics must contain exactly Ootang stations")
    if frame.duplicated(["date", "station"]).any():
        raise AutoV0InputError("Kinematics contains duplicate station/date rows")
    return frame.sort_values(["station", "date"], kind="stable").reset_index(drop=True)


def select_direct_candidate(
    station_frame: pd.DataFrame,
    *,
    station: str,
    fit_end_date: pd.Timestamp,
    profile: dict[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame]:
    fit = station_frame.loc[station_frame["date"] <= fit_end_date].copy()
    fit = fit.loc[fit["displacement_valid"].astype(bool)].reset_index(drop=True)
    base_record = {
        "case": "ootang",
        "station": station,
        "fit_start_date": fit["date"].min() if not fit.empty else pd.NaT,
        "fit_end_date": fit_end_date,
        "fit_rows": len(fit),
        "selection_status": "unavailable",
        "failure_reason": None,
        "selected_segment_count": None,
        "selected_bic": None,
        "one_segment_bic": None,
        "selected_segment_start_date": None,
        "selected_segment_end_date": None,
        "selected_segment_n": None,
        "first_break_date": None,
        "following_segment_slope_mm_per_day": None,
        "candidate_v_mm_per_day": None,
        "selected_velocity_n": None,
        "selected_velocity_mean_mm_per_day": None,
        "selected_velocity_sigma_mm_per_day": None,
        "candidate_v0_mm_per_day": None,
        "candidate_v0_status": "unavailable",
        "formal_warning_output": False,
        "candidate_v0_only": True,
        "vajont_used": False,
    }
    empty_segments = pd.DataFrame(columns=SEGMENT_COLUMNS)
    if len(fit) < MIN_SEGMENT_OBSERVATIONS:
        base_record["failure_reason"] = "insufficient_fit_rows"
        return base_record, empty_segments
    try:
        segmentation = segment_piecewise_linear_signal(
            fit.loc[:, ["date", "displacement"]],
            date_column="date",
            value_column="displacement",
        )
    except (TypeError, ValueError) as exc:
        base_record["failure_reason"] = str(exc)
        return base_record, empty_segments

    segments = segmentation["segments"]
    first = segments[0]
    following = segments[1] if len(segments) >= 2 else None
    base_record.update(
        {
            "selected_segment_count": segmentation["selected_segment_count"],
            "selected_bic": segmentation["selected_bic"],
            "one_segment_bic": segmentation["one_segment_bic"],
            "selected_segment_start_date": first["start_date"],
            "selected_segment_end_date": first["end_date"],
            "selected_segment_n": first["n_observations"],
            "first_break_date": None if following is None else following["start_date"],
            "following_segment_slope_mm_per_day": None
            if following is None
            else following["slope_mm_per_day"],
        }
    )
    if first["slope_mm_per_day"] <= 0:
        base_record["failure_reason"] = "nonpositive_initial_segment_slope"
        return base_record, _segment_records(station, fit_end_date, segments)
    if following is None:
        status = "stable_full_fit_baseline"
    elif following["slope_mm_per_day"] > first["slope_mm_per_day"]:
        status = "initial_segment_selected"
    else:
        base_record["failure_reason"] = "first_break_is_not_accelerating"
        return base_record, _segment_records(station, fit_end_date, segments)

    selected = fit.loc[
        fit["date"].between(first["start_date"], first["end_date"])
        & fit["velocity_status"].eq("valid")
        & np.isfinite(fit["velocity"])
    ]
    if len(selected) < 2:
        base_record["failure_reason"] = "insufficient_selected_velocity_rows"
        return base_record, _segment_records(station, fit_end_date, segments)
    velocities = selected["velocity"].to_numpy(dtype=float)
    mean_velocity = float(np.mean(velocities))
    sigma_velocity = float(np.std(velocities, ddof=1))
    v0 = float(max(1.5 * first["slope_mm_per_day"], first["slope_mm_per_day"] + 2.0 * sigma_velocity))
    if not np.isfinite(v0) or v0 <= 0:
        base_record["failure_reason"] = "nonpositive_or_nonfinite_v0"
        return base_record, _segment_records(station, fit_end_date, segments)
    base_record.update(
        {
            "selection_status": status,
            "failure_reason": None,
            "selected_velocity_n": len(selected),
            "selected_velocity_mean_mm_per_day": mean_velocity,
            "selected_velocity_sigma_mm_per_day": sigma_velocity,
            "candidate_v_mm_per_day": first["slope_mm_per_day"],
            "candidate_v0_mm_per_day": v0,
            "candidate_v0_status": status,
        }
    )
    return base_record, _segment_records(station, fit_end_date, segments)


def _segment_records(station: str, fit_end_date: pd.Timestamp, segments: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for index, segment in enumerate(segments):
        rows.append(
            {
                "case": "ootang",
                "station": station,
                "fit_start_date": segments[0]["start_date"],
                "fit_end_date": fit_end_date,
                "segment_index": index,
                "segment_start_date": segment["start_date"],
                "segment_end_date": segment["end_date"],
                "segment_n": segment["n_observations"],
                "intercept": segment["intercept"],
                "slope_mm_per_day": segment["slope_mm_per_day"],
                "sse": segment["sse"],
                "selected_segment": index == 0,
                "following_segment": index == 1,
                "formal_warning_output": False,
                "candidate_v0_only": True,
                "vajont_used": False,
            }
        )
    return pd.DataFrame(rows, columns=SEGMENT_COLUMNS)


def build_auto_v0_candidates(
    kinematics: pd.DataFrame,
    fit_end_dates: dict[str, pd.Timestamp],
    profile: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = _validate_kinematics(kinematics)
    candidates = []
    segments = []
    for station in OOTANG_STATIONS:
        candidate, station_segments = select_direct_candidate(
            frame.loc[frame["station"].eq(station)],
            station=station,
            fit_end_date=fit_end_dates[station],
            profile=profile,
        )
        candidates.append(candidate)
        if not station_segments.empty:
            segments.append(station_segments)
    candidates_frame = pd.DataFrame(candidates, columns=CANDIDATE_COLUMNS)
    segments_frame = (
        pd.concat(segments, ignore_index=True)
        if segments
        else pd.DataFrame(columns=SEGMENT_COLUMNS)
    )
    return candidates_frame, segments_frame


def _plot_candidates_impl(
    kinematics: pd.DataFrame,
    candidates: pd.DataFrame,
    segments: pd.DataFrame,
    output_png: Path,
    output_svg: Path,
) -> None:
    colors = {
        "raw": "#898781",
        "initial_segment_selected": "#2a78d6",
        "stable_full_fit_baseline": "#fab219",
        "unavailable": "#d03b3b",
        "following": "#eb6834",
    }
    fig, axes = plt.subplots(4, 2, figsize=(12, 12), sharex=False)
    axes = axes.ravel()
    for axis, station in zip(axes, OOTANG_STATIONS, strict=True):
        station_frame = kinematics.loc[kinematics["station"].eq(station)].copy()
        station_frame["date"] = pd.to_datetime(station_frame["date"])
        candidate = candidates.loc[candidates["station"].eq(station)].iloc[0]
        fit_frame = station_frame.loc[station_frame["date"] <= candidate["fit_end_date"]]
        fit_origin = pd.Timestamp(fit_frame["date"].iloc[0])
        axis.plot(
            fit_frame["date"],
            fit_frame["displacement"],
            color=colors["raw"],
            linewidth=1.0,
            label="fit displacement",
        )
        station_segments = segments.loc[segments["station"].eq(station)]
        for row in station_segments.itertuples(index=False):
            segment_frame = fit_frame.loc[
                fit_frame["date"].between(
                    pd.Timestamp(row.segment_start_date),
                    pd.Timestamp(row.segment_end_date),
                )
            ]
            elapsed = (
                segment_frame["date"] - fit_origin
            ).dt.total_seconds() / 86_400.0
            line_color = (
                colors["initial_segment_selected"]
                if row.selected_segment
                else colors["following"]
                if row.following_segment
                else colors["raw"]
            )
            axis.plot(
                segment_frame["date"],
                row.intercept + row.slope_mm_per_day * elapsed,
                color=line_color,
                linewidth=2.2 if row.selected_segment else 1.8,
                linestyle="-" if row.selected_segment or row.following_segment else "--",
            )
        if pd.notna(candidate["first_break_date"]):
            axis.axvline(
                pd.Timestamp(candidate["first_break_date"]),
                color=colors["following"],
                linewidth=1.0,
                linestyle=":",
            )
        status = str(candidate["selection_status"])
        status_color = colors.get(status, colors["unavailable"])
        v0_text = (
            "V0 unavailable"
            if pd.isna(candidate["candidate_v0_mm_per_day"])
            else f"V0={float(candidate['candidate_v0_mm_per_day']):.4g} mm/d"
        )
        axis.text(
            0.02,
            0.96,
            f"{status}\n{v0_text}",
            transform=axis.transAxes,
            va="top",
            color=status_color,
            fontsize=8,
            bbox={"facecolor": "white", "edgecolor": status_color, "alpha": 0.85},
        )
        axis.set_title(station, loc="left", fontweight="bold")
        axis.grid(alpha=0.2, linewidth=0.5)
        axis.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
        axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        axis.tick_params(axis="x", labelrotation=30, labelsize=7)
        axis.set_ylabel("displacement (mm)", fontsize=8)
    fig.suptitle(
        "Automatic fit-only V0 candidate: raw displacement BIC segmentation",
        fontsize=14,
        fontweight="bold",
    )
    fig.text(
        0.5,
        0.01,
        "Gray: raw fit displacement; blue: initial candidate segment; orange: following segment/break; labels are statuses, not warning levels.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0.03, 1, 0.96))
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, dpi=300, facecolor="white")
    fig.savefig(output_svg, facecolor="white", metadata={"Date": None})
    svg = output_svg.read_text(encoding="utf-8")
    output_svg.write_text(
        "\n".join(line.rstrip() for line in svg.splitlines()) + "\n",
        encoding="utf-8",
    )
    plt.close(fig)


def _plot_candidates(
    kinematics: pd.DataFrame,
    candidates: pd.DataFrame,
    segments: pd.DataFrame,
    output_png: Path,
    output_svg: Path,
) -> None:
    """Render with isolated deterministic plotting settings."""
    with matplotlib.rc_context(dict(matplotlib.rcParamsDefault)):
        plt.rcParams["svg.hashsalt"] = "ootang-auto-v0-direct-bai-perron-v1"
        _plot_candidates_impl(
            kinematics,
            candidates,
            segments,
            output_png,
            output_svg,
        )


def _output_record(path: Path, target: Path, rows: int | None = None) -> dict[str, Any]:
    record = {
        "path": _manifest_path(target),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }
    if rows is not None:
        record["n_rows"] = rows
    return record


def write_auto_v0_candidates(
    *,
    profile_path: Path = DEFAULT_PROFILE_PATH,
    kinematics_path: Path = DEFAULT_KINEMATICS_PATH,
    predictions_path: Path = DEFAULT_PREDICTIONS_PATH,
    forecast_manifest_path: Path = DEFAULT_FORECAST_MANIFEST_PATH,
    thresholds_path: Path = DEFAULT_THRESHOLDS_PATH,
    v4_manifest_path: Path = DEFAULT_V4_MANIFEST_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> Path:
    """Write automatic fit-only V0 candidate diagnostics."""
    profile_path = Path(profile_path).resolve()
    kinematics_path = Path(kinematics_path).resolve()
    predictions_path = Path(predictions_path).resolve()
    forecast_manifest_path = Path(forecast_manifest_path).resolve()
    thresholds_path = Path(thresholds_path).resolve()
    v4_manifest_path = Path(v4_manifest_path).resolve()
    output_dir = Path(output_dir).resolve()
    _validate_output_dir(output_dir)
    profile = load_auto_v0_profile(profile_path)
    forecast_source = base.validate_forecast_lineage(
        predictions_path, forecast_manifest_path
    )
    v4_source = base.validate_v4_lineage(
        kinematics_path=kinematics_path,
        predictions_path=predictions_path,
        thresholds_path=thresholds_path,
        manifest_path=v4_manifest_path,
    )
    kinematics = pd.read_csv(
        kinematics_path,
        usecols=(
            "case", "date", "station", "displacement", "displacement_valid",
            "velocity", "velocity_status",
        ),
    )
    fit_end_dates = _load_fit_end_dates(predictions_path)
    candidates, segments = build_auto_v0_candidates(
        kinematics, fit_end_dates, profile
    )
    if len(candidates) != len(OOTANG_STATIONS):
        raise AutoV0OutputError("Automatic V0 must emit one record per station")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".ootang-auto-v0-direct-bai-perron-", dir=ROOT
    ) as directory:
        staging = Path(directory)
        staged_candidates = staging / "candidates.csv"
        staged_segments = staging / "segments.csv"
        staged_png = staging / "candidate_diagnostics.png"
        staged_svg = staging / "candidate_diagnostics.svg"
        staged_candidates.parent.mkdir(parents=True, exist_ok=True)
        candidates.to_csv(staged_candidates, index=False, lineterminator="\n")
        segments.to_csv(staged_segments, index=False, lineterminator="\n")
        _plot_candidates(
            kinematics,
            candidates,
            segments,
            staged_png,
            staged_svg,
        )
        output_records = {
            "candidates": _output_record(staged_candidates, output_dir / "candidates.csv", len(candidates)),
            "segments": _output_record(staged_segments, output_dir / "segments.csv", len(segments)),
            "figure_png": _output_record(staged_png, output_dir / "candidate_diagnostics.png"),
            "figure_svg": _output_record(staged_svg, output_dir / "candidate_diagnostics.svg"),
        }
        manifest = {
            "schema_version": 1,
            "artifact_kind": ARTIFACT_KIND,
            "artifact_status": ARTIFACT_STATUS,
            "case": "ootang",
            "formal_warning_output": False,
            "candidate_v0_only": True,
            "vajont_used": False,
            "default_pipeline_member": False,
            "profile": {
                "id": profile["profile_id"],
                "version": profile["profile_version"],
                "path": _manifest_path(profile_path),
                "file_sha256": _sha256_file(profile_path),
                "content_sha256": _canonical_json_sha256(profile),
            },
            "git_commit": base._git_commit(),
            "git_worktree_dirty": base._git_worktree_dirty(),
            "source_inputs": {
                "kinematics": {"path": _manifest_path(kinematics_path), "sha256": _sha256_file(kinematics_path)},
                "predictions": {"path": _manifest_path(predictions_path), "sha256": _sha256_file(predictions_path)},
                "forecast_manifest": forecast_source,
                "v4_manifest": v4_source,
            },
            "fit_end_dates": {station: date.date().isoformat() for station, date in fit_end_dates.items()},
            "method": profile["segmentation"],
            "v0_contract": profile["v0"],
            "selection_status_counts": candidates["selection_status"].value_counts().to_dict(),
            "candidate_v0_status_counts": candidates["candidate_v0_status"].value_counts().to_dict(),
            "outputs": output_records,
            "not_claimed": profile["not_claimed"],
        }
        staged_manifest = staging / "manifest.json"
        staged_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        targets = {
            "candidates": output_dir / "candidates.csv",
            "segments": output_dir / "segments.csv",
            "figure_png": output_dir / "candidate_diagnostics.png",
            "figure_svg": output_dir / "candidate_diagnostics.svg",
            "manifest": output_dir / "manifest.json",
        }
        replacements = (
            FileReplacement(staged_candidates, targets["candidates"]),
            FileReplacement(staged_segments, targets["segments"]),
            FileReplacement(staged_png, targets["figure_png"]),
            FileReplacement(staged_svg, targets["figure_svg"]),
            FileReplacement(staged_manifest, targets["manifest"]),
        )
        promote_staged_files(replacements)
    return targets["manifest"]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the automatic fit-only raw-displacement V0 candidate diagnostic."
    )
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH)
    parser.add_argument("--kinematics", type=Path, default=DEFAULT_KINEMATICS_PATH)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS_PATH)
    parser.add_argument("--forecast-manifest", type=Path, default=DEFAULT_FORECAST_MANIFEST_PATH)
    parser.add_argument("--thresholds", type=Path, default=DEFAULT_THRESHOLDS_PATH)
    parser.add_argument("--v4-manifest", type=Path, default=DEFAULT_V4_MANIFEST_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    manifest = write_auto_v0_candidates(
        profile_path=args.profile,
        kinematics_path=args.kinematics,
        predictions_path=args.predictions,
        forecast_manifest_path=args.forecast_manifest,
        thresholds_path=args.thresholds,
        v4_manifest_path=args.v4_manifest,
    )
    print(f"[auto-v0] exploratory Ootang candidate bundle: {manifest}")


if __name__ == "__main__":
    main()


__all__ = [
    "ARTIFACT_KIND",
    "ARTIFACT_STATUS",
    "CANDIDATE_COLUMNS",
    "DEFAULT_OUTPUT_DIR",
    "SEGMENT_COLUMNS",
    "AutoV0InputError",
    "AutoV0OutputError",
    "AutoV0ProfileError",
    "build_auto_v0_candidates",
    "load_auto_v0_profile",
    "segment_piecewise_linear_signal",
    "select_direct_candidate",
    "write_auto_v0_candidates",
]
