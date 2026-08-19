"""Display-only v5 candidate branch using automatic V0 availability."""

from __future__ import annotations

import argparse
import hashlib
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

from warning import auto_v0_direct_bai_perron as auto_v0  # noqa: E402
from warning.draft_evidence import FileReplacement, OOTANG_STATIONS, promote_staged_files  # noqa: E402
from warning import ootang_ngboost_interval_proxy_pilot as base  # noqa: E402
from warning.interval_state import classify_observed_interval_states  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = ROOT / "config" / "ootang_v5_candidate_display.v1.json"
DEFAULT_V0_CANDIDATES_PATH = auto_v0.DEFAULT_OUTPUT_DIR / "candidates.csv"
DEFAULT_V0_SEGMENTS_PATH = auto_v0.DEFAULT_OUTPUT_DIR / "segments.csv"
DEFAULT_V0_MANIFEST_PATH = auto_v0.DEFAULT_OUTPUT_DIR / "manifest.json"
DEFAULT_KINEMATICS_PATH = base.DEFAULT_KINEMATICS_PATH
DEFAULT_PREDICTIONS_PATH = base.DEFAULT_PREDICTIONS_PATH
DEFAULT_FORECAST_MANIFEST_PATH = base.DEFAULT_FORECAST_MANIFEST_PATH
DEFAULT_V4_STATION_PATH = ROOT / "figures" / "warning_operational_draft_v4" / "ootang_operational_station_timeline.csv"
DEFAULT_V4_SITE_PATH = ROOT / "figures" / "warning_operational_draft_v4" / "ootang_operational_site_timeline.csv"
DEFAULT_V4_MANIFEST_PATH = base.DEFAULT_V4_MANIFEST_PATH
DEFAULT_THRESHOLDS_PATH = base.DEFAULT_THRESHOLDS_PATH
DEFAULT_OUTPUT_DIR = ROOT / "figures" / "v5_candidate_display_ootang_v1"
ARTIFACT_KIND = "ootang_v5_candidate_display"
ARTIFACT_STATUS = "exploratory_candidate_display_not_formal"
V0_AVAILABLE_STATUSES = frozenset(
    {"initial_segment_selected", "stable_full_fit_baseline"}
)
BRANCH_AVAILABLE_STATUS = "candidate_available"
BRANCH_NOT_APPLICABLE_STATUS = "not_applicable_v0_unavailable"
TIMELINE_COLUMNS = (
    "case",
    "date",
    "station",
    "split",
    "actual_displacement_mm",
    "v0_candidate_status",
    "candidate_v0_mm_per_day",
    "v0_candidate_available",
    "v5_kinematic_branch_status",
    "velocity_mm_per_day",
    "velocity_status",
    "velocity_ratio",
    "velocity_ratio_status",
    "delta_v_mm_per_day",
    "delta_v_state",
    "delta_v_near_zero_tau_mm_per_day",
    "tangent_angle_degree",
    "tangent_angle_status",
    "interval_level",
    "v4_reference_final_color",
    "v4_reference_candidate_color",
    "v4_reference_site_fusion_status",
    "v4_reference_site_confirmed_color",
    "v4_reference_local_max_color",
    "candidate_display_only",
    "v5_fusion_output",
    "formal_warning_output",
    "vajont_used",
)
SUMMARY_COLUMNS = (
    "case",
    "station",
    "v0_candidate_status",
    "candidate_v0_mm_per_day",
    "fit_start_date",
    "fit_end_date",
    "v5_kinematic_branch_status",
    "result_rows",
    "v0_available_rows",
    "v0_not_applicable_rows",
    "valid_delta_v_rows",
    "candidate_display_only",
    "v5_fusion_output",
    "formal_warning_output",
    "vajont_used",
)


class CandidateDisplayProfileError(ValueError):
    """Raised when the candidate-display profile changes."""


class CandidateDisplayInputError(RuntimeError):
    """Raised when a source violates the candidate-display contract."""


class CandidateDisplayOutputError(RuntimeError):
    """Raised when a display-only output violates its contract."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _resolve_artifact_path(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def _canonical_json_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _load_json(path: Path, source_name: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CandidateDisplayInputError(f"Missing {source_name}: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateDisplayInputError(f"Invalid {source_name}: {path}") from exc
    if not isinstance(payload, dict):
        raise CandidateDisplayInputError(f"{source_name} must be an object")
    return payload


def _require_equal(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise CandidateDisplayProfileError(f"{name} must be {expected!r}; found {actual!r}")


def load_candidate_display_profile(path: Path = DEFAULT_PROFILE_PATH) -> dict[str, Any]:
    profile = _load_json(Path(path), "candidate display profile")
    fixed = {
        "profile_id": "ootang-v5-candidate-display-v1",
        "profile_version": "1.0-display",
        "status": "exploratory_candidate_display",
        "case": "ootang",
        "formal_warning_output": False,
        "candidate_display_only": True,
        "v5_fusion_output": False,
        "vajont_used": False,
        "default_pipeline_member": False,
        "stations": list(OOTANG_STATIONS),
    }
    for name, expected in fixed.items():
        _require_equal(profile.get(name), expected, name)
    source_contract = profile.get("source_contract")
    expected_contract = {
        "v0_candidates": "figures/auto_v0_direct_bai_perron_ootang_v1/candidates.csv",
        "v0_segments": "figures/auto_v0_direct_bai_perron_ootang_v1/segments.csv",
        "v0_manifest": "figures/auto_v0_direct_bai_perron_ootang_v1/manifest.json",
        "kinematics": "data/ootang_kinematics_long.csv",
        "predictions": "figures/convlstm/forecast_predictions.csv",
        "forecast_manifest": "figures/convlstm/forecast_run_manifest.json",
        "v4_station_reference": "figures/warning_operational_draft_v4/ootang_operational_station_timeline.csv",
        "v4_site_reference": "figures/warning_operational_draft_v4/ootang_operational_site_timeline.csv",
        "v4_thresholds": "figures/warning_operational_draft_v4/ootang_operational_thresholds.csv",
        "v4_manifest": "figures/warning_operational_draft_v4/ootang_operational_run_manifest.json",
        "fit_only_v0_selection": True,
        "no_v0_fallback": True,
        "no_manual_override": True,
        "no_v4_fusion_call": True,
    }
    _require_equal(source_contract, expected_contract, "source_contract")
    expected_features = {
        "display_fields": [
            "actual_displacement_mm",
            "velocity_mm_per_day",
            "delta_v_mm_per_day",
            "delta_v_state",
            "tangent_angle_degree",
            "interval_level",
        ],
        "v0_dependent_fields": ["velocity_ratio", "tangent_angle_degree"],
        "availability_fields": [
            "v0_candidate_available",
            "v5_kinematic_branch_status",
            "velocity_ratio_status",
            "tangent_angle_status",
        ],
        "unavailable_policy": "not_applicable_without_inference",
        "delta_v_near_zero_tau_formula": (
            "1.4826*median_absolute_deviation_about_fit_median"
        ),
    }
    _require_equal(profile.get("features"), expected_features, "features")
    outputs = profile.get("outputs")
    expected_outputs = {
        "directory": "figures/v5_candidate_display_ootang_v1",
        "timeline": "candidate_timeline.csv",
        "summary": "candidate_summary.csv",
        "figure_png": "candidate_display.png",
        "figure_svg": "candidate_display.svg",
        "manifest": "manifest.json",
    }
    _require_equal(outputs, expected_outputs, "outputs")
    expected_not_claimed = [
        "v5_fused_warning_output",
        "formal_warning_output",
        "ngboost_inference_or_probability_output",
        "expert_or_field_truth",
        "validated_formal_v0",
        "v4_replacement_or_change",
        "site_level_candidate_color",
        "unavailable_v0_imputation",
        "causal_interpretation",
        "external_confirmation",
        "vajont_use_or_validation",
    ]
    _require_equal(profile.get("not_claimed"), expected_not_claimed, "not_claimed")
    return profile


def _validate_output_dir(output_dir: Path) -> None:
    resolved = output_dir.resolve()
    if resolved.is_relative_to(ROOT.resolve()) and resolved != DEFAULT_OUTPUT_DIR.resolve():
        raise CandidateDisplayOutputError("Candidate display output_dir is fixed")
    if resolved.exists() and resolved.is_file():
        raise CandidateDisplayOutputError("Candidate display output_dir must be a directory")


def _validate_v0_manifest(
    manifest_path: Path,
    candidates_path: Path,
    segments_path: Path,
) -> dict[str, Any]:
    manifest = _load_json(manifest_path, "automatic V0 manifest")
    required = {
        "artifact_kind": auto_v0.ARTIFACT_KIND,
        "artifact_status": auto_v0.ARTIFACT_STATUS,
        "case": "ootang",
        "formal_warning_output": False,
        "candidate_v0_only": True,
        "vajont_used": False,
        "default_pipeline_member": False,
    }
    for name, expected in required.items():
        if manifest.get(name) != expected:
            raise CandidateDisplayInputError(
                f"Automatic V0 manifest has invalid {name}"
            )

    method = manifest.get("method")
    method_contract = {
        "failure_policy": "unavailable_with_reason",
        "initial_segment_rule": (
            "positive_first_slope_and_immediately_following_slope_greater"
        ),
        "single_segment_rule": "positive_full_fit_is_stable_full_fit_baseline",
        "mvif_prerequisite": False,
        "kmeans_fallback": False,
    }
    if not isinstance(method, dict):
        raise CandidateDisplayInputError("Automatic V0 manifest lacks method")
    for name, expected in method_contract.items():
        if method.get(name) != expected:
            raise CandidateDisplayInputError(
                f"Automatic V0 method has invalid {name}"
            )

    not_claimed = manifest.get("not_claimed")
    required_nonclaims = {
        "calibration_or_test_selection",
        "manual_stage_selection",
        "kmeans_v0_promotion",
        "formal_warning_output",
        "vajont_use_or_validation",
    }
    if not isinstance(not_claimed, list) or not required_nonclaims.issubset(
        not_claimed
    ):
        raise CandidateDisplayInputError(
            "Automatic V0 manifest does not preserve fit-only non-claims"
        )

    profile_record = manifest.get("profile")
    if not isinstance(profile_record, dict) or not isinstance(
        profile_record.get("path"), str
    ):
        raise CandidateDisplayInputError("Automatic V0 manifest lacks profile")
    source_profile_path = _resolve_artifact_path(profile_record["path"])
    if profile_record.get("file_sha256") != _sha256_file(source_profile_path):
        raise CandidateDisplayInputError(
            "Automatic V0 profile does not match its manifest"
        )
    auto_v0.load_auto_v0_profile(source_profile_path)

    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise CandidateDisplayInputError("Automatic V0 manifest lacks outputs")
    for name, path in (
        ("candidates", candidates_path),
        ("segments", segments_path),
    ):
        record = outputs.get(name)
        if not isinstance(record, dict) or record.get("sha256") != _sha256_file(path):
            raise CandidateDisplayInputError(
                f"Automatic V0 {name} does not match its manifest"
            )
    return {
        "path": _manifest_path(manifest_path),
        "sha256": _sha256_file(manifest_path),
        "artifact_kind": manifest["artifact_kind"],
        "artifact_status": manifest["artifact_status"],
        "profile_sha256": profile_record["file_sha256"],
        "fit_only_contract_verified": True,
    }


def _validate_v0_candidates(candidates: pd.DataFrame) -> pd.DataFrame:
    required = {
        "case",
        "station",
        "fit_start_date",
        "fit_end_date",
        "selection_status",
        "candidate_v0_mm_per_day",
        "candidate_v0_status",
        "candidate_v0_only",
        "formal_warning_output",
        "vajont_used",
    }
    missing = required.difference(candidates.columns)
    if missing:
        raise CandidateDisplayInputError(
            f"V0 candidates missing columns: {sorted(missing)}"
        )
    forbidden = {
        "split",
        "calibration_start_date",
        "calibration_end_date",
        "test_start_date",
        "test_end_date",
        "manual_stage_start_date",
        "manual_stage_end_date",
    }
    present_forbidden = forbidden.intersection(candidates.columns)
    if present_forbidden:
        raise CandidateDisplayInputError(
            "V0 candidates expose forbidden selection fields: "
            f"{sorted(present_forbidden)}"
        )

    frame = candidates.copy()
    frame["station"] = frame["station"].astype("string").str.strip()
    frame["case"] = frame["case"].astype("string").str.strip()
    frame["fit_start_date"] = pd.to_datetime(
        frame["fit_start_date"], errors="coerce"
    )
    frame["fit_end_date"] = pd.to_datetime(frame["fit_end_date"], errors="coerce")
    frame["candidate_v0_mm_per_day"] = pd.to_numeric(
        frame["candidate_v0_mm_per_day"], errors="coerce"
    )
    if not frame["case"].eq("ootang").all():
        raise CandidateDisplayInputError("V0 candidates must be case=ootang")
    if (
        set(frame["station"].dropna().astype(str)) != set(OOTANG_STATIONS)
        or len(frame) != len(OOTANG_STATIONS)
    ):
        raise CandidateDisplayInputError(
            "V0 candidates must contain one row per Ootang station"
        )
    if frame["station"].duplicated().any():
        raise CandidateDisplayInputError("V0 candidates contain duplicate stations")
    if frame["fit_start_date"].isna().any() or frame["fit_end_date"].isna().any():
        raise CandidateDisplayInputError("V0 candidates contain invalid fit dates")
    if not frame["candidate_v0_only"].eq(True).all():
        raise CandidateDisplayInputError("V0 candidates must be candidate-only")
    if (
        not frame["formal_warning_output"].eq(False).all()
        or not frame["vajont_used"].eq(False).all()
    ):
        raise CandidateDisplayInputError(
            "V0 candidates must remain nonformal and Ootang-only"
        )

    allowed = V0_AVAILABLE_STATUSES | {"unavailable"}
    if not set(frame["selection_status"]) <= allowed:
        raise CandidateDisplayInputError("V0 candidates contain an unknown status")
    if not frame["candidate_v0_status"].eq(frame["selection_status"]).all():
        raise CandidateDisplayInputError(
            "V0 candidate status must match the selection status"
        )
    available = frame["selection_status"].isin(V0_AVAILABLE_STATUSES)
    available_v0 = frame.loc[available, "candidate_v0_mm_per_day"]
    if available_v0.empty or not (
        np.isfinite(available_v0).all() and available_v0.gt(0).all()
    ):
        raise CandidateDisplayInputError(
            "Available V0 candidates must be finite and positive"
        )
    if frame.loc[~available, "candidate_v0_mm_per_day"].notna().any():
        raise CandidateDisplayInputError(
            "Unavailable stations must not contain an inferred V0"
        )
    return frame.sort_values("station", kind="stable").reset_index(drop=True)


def _validate_v0_segments(segments: pd.DataFrame) -> pd.DataFrame:
    required = {
        "case",
        "station",
        "fit_start_date",
        "fit_end_date",
        "segment_start_date",
        "segment_end_date",
        "selected_segment",
        "following_segment",
        "formal_warning_output",
        "candidate_v0_only",
        "vajont_used",
    }
    missing = required.difference(segments.columns)
    if missing:
        raise CandidateDisplayInputError(
            f"V0 segments missing columns: {sorted(missing)}"
        )
    forbidden = {
        "split",
        "calibration_start_date",
        "calibration_end_date",
        "test_start_date",
        "test_end_date",
        "manual_stage_start_date",
        "manual_stage_end_date",
    }
    present_forbidden = forbidden.intersection(segments.columns)
    if present_forbidden:
        raise CandidateDisplayInputError(
            "V0 segments expose forbidden selection fields: "
            f"{sorted(present_forbidden)}"
        )

    frame = segments.copy()
    frame["station"] = frame["station"].astype("string").str.strip()
    frame["case"] = frame["case"].astype("string").str.strip()
    if frame.empty or not frame["case"].eq("ootang").all():
        raise CandidateDisplayInputError("V0 segments must be nonempty and Ootang-only")
    if not set(frame["station"].dropna().astype(str)) <= set(OOTANG_STATIONS):
        raise CandidateDisplayInputError("V0 segments contain an unknown station")
    if not frame["candidate_v0_only"].eq(True).all():
        raise CandidateDisplayInputError("V0 segments must be candidate-only")
    if (
        not frame["formal_warning_output"].eq(False).all()
        or not frame["vajont_used"].eq(False).all()
    ):
        raise CandidateDisplayInputError(
            "V0 segments must remain nonformal and Ootang-only"
        )
    return frame


def _validate_v4_reference_lineage(
    manifest_path: Path,
    station_path: Path,
    site_path: Path,
) -> dict[str, Any]:
    manifest = _load_json(manifest_path, "v4 operational manifest")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise CandidateDisplayInputError("v4 manifest lacks output records")
    records = {}
    for name, path, expected_rows in (
        ("station_timeline", station_path, 4112),
        ("site_timeline", site_path, 514),
    ):
        record = outputs.get(name)
        if (
            not isinstance(record, dict)
            or record.get("sha256") != _sha256_file(path)
            or record.get("n_rows") != expected_rows
        ):
            raise CandidateDisplayInputError(
                f"v4 {name} does not match its manifest"
            )
        records[name] = {
            "path": _manifest_path(path),
            "sha256": record["sha256"],
            "n_rows": expected_rows,
        }
    return records


def _validate_result_grid(frame: pd.DataFrame) -> None:
    if frame["date"].isna().any() or frame["station"].isna().any():
        raise CandidateDisplayInputError("Result rows contain invalid dates or stations")
    if frame.duplicated(["date", "station"]).any():
        raise CandidateDisplayInputError("Result rows contain duplicate station/date pairs")
    if set(frame["split"].astype(str)) != {"calibration", "test"}:
        raise CandidateDisplayInputError(
            "Candidate display accepts calibration/test result rows only"
        )
    if set(frame["station"].astype(str)) != set(OOTANG_STATIONS):
        raise CandidateDisplayInputError(
            "Candidate display must preserve all Ootang stations"
        )
    if len(frame) != 4112 or frame["date"].nunique() != 514:
        raise CandidateDisplayOutputError(
            "Candidate timeline must contain 514 dates and 4,112 rows"
        )
    if not frame.groupby("station").size().eq(514).all():
        raise CandidateDisplayOutputError(
            "Candidate timeline must contain 514 rows per station"
        )
    if not frame.groupby("date").size().eq(len(OOTANG_STATIONS)).all():
        raise CandidateDisplayOutputError(
            "Candidate timeline must preserve all stations on every date"
        )


def _fit_delta_v_tolerance(kinematics: pd.DataFrame, station: str, fit_end: pd.Timestamp) -> float:
    group = kinematics.loc[
        kinematics["station"].eq(station)
        & (kinematics["date"] <= fit_end)
        & kinematics["delta_v_status"].eq("valid")
    ]
    values = pd.to_numeric(group["delta_v"], errors="coerce").dropna().to_numpy(dtype=float)
    if len(values) == 0:
        return float("nan")
    median = float(np.median(values))
    return float(1.4826 * np.median(np.abs(values - median)))


def _delta_v_state(value: float, tau: float) -> str | None:
    if not np.isfinite(value) or not np.isfinite(tau):
        return None
    if tau <= 0:
        return "negative" if value < 0 else "positive" if value > 0 else "near_zero"
    if value < -tau:
        return "negative"
    if value > tau:
        return "positive"
    return "near_zero"


def build_candidate_display_timeline(
    *,
    profile: dict[str, Any],
    v0_candidates: pd.DataFrame,
    kinematics: pd.DataFrame,
    predictions: pd.DataFrame,
    v4_station: pd.DataFrame,
    v4_site: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    _require_equal(profile.get("case"), "ootang", "profile.case")
    _require_equal(
        profile.get("candidate_display_only"), True, "profile.candidate_display_only"
    )
    candidates = _validate_v0_candidates(v0_candidates)

    k = kinematics.copy()
    k["date"] = pd.to_datetime(k["date"], errors="coerce")
    k["station"] = k["station"].astype("string").str.strip()
    if "case" in k and not k["case"].astype("string").str.strip().eq("ootang").all():
        raise CandidateDisplayInputError("Candidate display kinematics must be Ootang-only")
    if k.duplicated(["date", "station"]).any():
        raise CandidateDisplayInputError("Kinematics contain duplicate station/date rows")
    k = k.loc[
        :,
        [
            "date",
            "station",
            "velocity",
            "velocity_status",
            "delta_v",
            "delta_v_status",
        ],
    ]
    k["velocity"] = pd.to_numeric(k["velocity"], errors="coerce")
    k["delta_v"] = pd.to_numeric(k["delta_v"], errors="coerce")

    p = predictions.copy()
    p["date"] = pd.to_datetime(p["date"], errors="coerce")
    p["station"] = p["station"].astype("string").str.strip()
    p = p.loc[p["split"].isin(["calibration", "test"])].copy()
    _validate_result_grid(p)
    if candidates["fit_end_date"].max() >= p["date"].min():
        raise CandidateDisplayInputError(
            "Automatic V0 fit boundary must precede all displayed result rows"
        )
    p = classify_observed_interval_states(p)

    station_reference = v4_station.copy()
    station_reference["date"] = pd.to_datetime(
        station_reference["date"], errors="coerce"
    )
    station_reference["station"] = (
        station_reference["station"].astype("string").str.strip()
    )
    station_reference = station_reference.loc[
        :, ["date", "station", "final_color", "candidate_color"]
    ].rename(
        columns={
            "final_color": "v4_reference_final_color",
            "candidate_color": "v4_reference_candidate_color",
        }
    )

    site_reference = v4_site.copy()
    site_reference["date"] = pd.to_datetime(
        site_reference["date"], errors="coerce"
    )
    site_reference = site_reference.loc[
        :,
        [
            "date",
            "site_fusion_status",
            "site_confirmed_color",
            "local_max_candidate_color",
        ],
    ].rename(
        columns={
            "site_fusion_status": "v4_reference_site_fusion_status",
            "site_confirmed_color": "v4_reference_site_confirmed_color",
            "local_max_candidate_color": "v4_reference_local_max_color",
        }
    )

    timeline = p.merge(k, on=["date", "station"], how="left", validate="one_to_one")
    timeline = timeline.merge(
        station_reference,
        on=["date", "station"],
        how="left",
        validate="one_to_one",
    )
    timeline = timeline.merge(
        site_reference, on="date", how="left", validate="many_to_one"
    )
    timeline = timeline.merge(
        candidates.loc[
            :,
            [
                "station",
                "selection_status",
                "candidate_v0_mm_per_day",
                "fit_start_date",
                "fit_end_date",
            ],
        ].rename(columns={"selection_status": "v0_candidate_status"}),
        on="station",
        how="left",
        validate="many_to_one",
    )
    if timeline["v0_candidate_status"].isna().any():
        raise CandidateDisplayInputError(
            "Every result row must map to a V0 candidate status"
        )
    if (
        timeline[["velocity", "delta_v"]].isna().any().any()
        or not timeline["velocity_status"].eq("valid").all()
        or not timeline["delta_v_status"].eq("valid").all()
    ):
        raise CandidateDisplayInputError(
            "Every displayed result row must have valid raw velocity and delta V"
        )
    if timeline[
        [
            "v4_reference_final_color",
            "v4_reference_candidate_color",
            "v4_reference_site_fusion_status",
            "v4_reference_local_max_color",
        ]
    ].isna().any().any():
        raise CandidateDisplayInputError(
            "Every displayed result row must map to historical v4 references"
        )
    allowed_site_statuses = {"valid", "candidate_not_site_confirmed"}
    if not set(timeline["v4_reference_site_fusion_status"]) <= allowed_site_statuses:
        raise CandidateDisplayInputError("Historical v4 site status is invalid")
    confirmed = timeline["v4_reference_site_fusion_status"].eq("valid")
    if timeline.loc[
        confirmed, "v4_reference_site_confirmed_color"
    ].isna().any():
        raise CandidateDisplayInputError(
            "Confirmed historical v4 site rows require a color"
        )

    tau_by_station = {
        station: _fit_delta_v_tolerance(
            k,
            station,
            pd.Timestamp(
                candidates.loc[
                    candidates["station"].eq(station), "fit_end_date"
                ].iloc[0]
            ),
        )
        for station in OOTANG_STATIONS
    }
    if not all(np.isfinite(value) and value >= 0 for value in tau_by_station.values()):
        raise CandidateDisplayInputError(
            "Every station requires a finite fit-only delta V tolerance"
        )

    timeline["delta_v_near_zero_tau_mm_per_day"] = timeline["station"].map(
        tau_by_station
    )
    timeline["actual_displacement_mm"] = pd.to_numeric(
        timeline["actual"], errors="coerce"
    )
    timeline["velocity_mm_per_day"] = timeline["velocity"]
    timeline["delta_v_mm_per_day"] = timeline["delta_v"]
    timeline["delta_v_state"] = [
        _delta_v_state(value, tau_by_station[str(station)])
        for station, value in zip(
            timeline["station"], timeline["delta_v"], strict=True
        )
    ]

    available = timeline["v0_candidate_status"].isin(V0_AVAILABLE_STATUSES)
    timeline["v0_candidate_available"] = available
    timeline["v5_kinematic_branch_status"] = np.where(
        available, BRANCH_AVAILABLE_STATUS, BRANCH_NOT_APPLICABLE_STATUS
    )
    timeline["velocity_ratio"] = np.nan
    timeline.loc[available, "velocity_ratio"] = (
        timeline.loc[available, "velocity_mm_per_day"]
        / timeline.loc[available, "candidate_v0_mm_per_day"]
    )
    timeline["velocity_ratio_status"] = timeline[
        "v5_kinematic_branch_status"
    ]
    timeline["tangent_angle_degree"] = np.nan
    timeline.loc[available, "tangent_angle_degree"] = (
        base.compute_improved_tangent_angle_degree(
            timeline.loc[available, "velocity_mm_per_day"],
            timeline.loc[available, "candidate_v0_mm_per_day"],
        )
    )
    timeline["tangent_angle_status"] = timeline[
        "v5_kinematic_branch_status"
    ]
    timeline["candidate_display_only"] = True
    timeline["v5_fusion_output"] = False
    timeline["formal_warning_output"] = False
    timeline["vajont_used"] = False

    result = timeline.loc[
        :, ["date", "station", "split", *TIMELINE_COLUMNS[4:]]
    ].copy()
    result.insert(0, "case", "ootang")
    result = result.sort_values(["date", "station"], kind="stable").reset_index(
        drop=True
    )
    _validate_result_grid(result)

    summary_rows = []
    for station in OOTANG_STATIONS:
        candidate = candidates.loc[candidates["station"].eq(station)].iloc[0]
        rows = result.loc[result["station"].eq(station)]
        branch_statuses = rows["v5_kinematic_branch_status"].unique().tolist()
        if len(branch_statuses) != 1:
            raise CandidateDisplayOutputError(
                f"Station {station} has inconsistent branch availability"
            )
        summary_rows.append(
            {
                "case": "ootang",
                "station": station,
                "v0_candidate_status": candidate["selection_status"],
                "candidate_v0_mm_per_day": candidate[
                    "candidate_v0_mm_per_day"
                ],
                "fit_start_date": candidate["fit_start_date"],
                "fit_end_date": candidate["fit_end_date"],
                "v5_kinematic_branch_status": branch_statuses[0],
                "result_rows": len(rows),
                "v0_available_rows": int(rows["v0_candidate_available"].sum()),
                "v0_not_applicable_rows": int(
                    (~rows["v0_candidate_available"]).sum()
                ),
                "valid_delta_v_rows": int(rows["delta_v_state"].notna().sum()),
                "candidate_display_only": True,
                "v5_fusion_output": False,
                "formal_warning_output": False,
                "vajont_used": False,
            }
        )
    summary = pd.DataFrame(summary_rows, columns=SUMMARY_COLUMNS)
    return result, summary


def _plot_candidate_display(
    timeline: pd.DataFrame,
    summary: pd.DataFrame,
    output_png: Path,
    output_svg: Path,
) -> None:
    with matplotlib.rc_context(dict(matplotlib.rcParamsDefault)):
        plt.rcParams["svg.hashsalt"] = "ootang-v5-candidate-display-v1"
        focus = ["MJ1", "MJ3"]
        fig = plt.figure(figsize=(10.5, 14.0))
        grid = fig.add_gridspec(
            6,
            2,
            height_ratios=(1.0, 1.0, 1.0, 1.0, 1.0, 1.15),
            hspace=0.42,
            wspace=0.25,
        )
        axes = np.empty((5, 2), dtype=object)
        for metric_index in range(5):
            for station_index in range(2):
                shared_axis = axes[0, station_index] if metric_index else None
                axes[metric_index, station_index] = fig.add_subplot(
                    grid[metric_index, station_index], sharex=shared_axis
                )

        for station_index, station in enumerate(focus):
            frame = timeline.loc[timeline["station"].eq(station)].sort_values(
                "date"
            )
            candidate = summary.loc[summary["station"].eq(station)].iloc[0]
            date = pd.to_datetime(frame["date"])
            candidate_v0 = float(candidate["candidate_v0_mm_per_day"])

            axes[0, station_index].plot(
                date,
                frame["actual_displacement_mm"],
                color="#898781",
                linewidth=1.4,
            )
            axes[0, station_index].set_title(
                f"{station} · observed displacement", loc="left", fontsize=9
            )
            axes[0, station_index].set_ylabel("displacement (mm)", fontsize=8)

            axes[1, station_index].plot(
                date,
                frame["velocity_mm_per_day"],
                color="#2a78d6",
                linewidth=1.4,
                label="raw velocity",
            )
            axes[1, station_index].axhline(
                candidate_v0,
                color="#eb6834",
                linewidth=1.2,
                linestyle="--",
                label=f"candidate V0={candidate_v0:.4f}",
            )
            axes[1, station_index].set_title(
                f"{station} · velocity and automatic V0", loc="left", fontsize=9
            )
            axes[1, station_index].set_ylabel("mm/day", fontsize=8)
            axes[1, station_index].legend(
                loc="upper left", frameon=False, fontsize=7, handlelength=2.4
            )

            axes[2, station_index].plot(
                date,
                frame["delta_v_mm_per_day"],
                color="#1baf7a",
                linewidth=1.4,
            )
            tau = float(frame["delta_v_near_zero_tau_mm_per_day"].iloc[0])
            axes[2, station_index].axhline(
                tau, color="#898781", linewidth=0.8, linestyle=":"
            )
            axes[2, station_index].axhline(
                -tau, color="#898781", linewidth=0.8, linestyle=":"
            )
            axes[2, station_index].axhline(0, color="#c3c2b7", linewidth=0.8)
            axes[2, station_index].set_title(
                f"{station} · ΔV with fit-only ±τ", loc="left", fontsize=9
            )
            axes[2, station_index].set_ylabel("mm/day", fontsize=8)

            axes[3, station_index].plot(
                date,
                frame["tangent_angle_degree"],
                color="#eb6834",
                linewidth=1.4,
            )
            axes[3, station_index].set_title(
                f"{station} · candidate tangent angle", loc="left", fontsize=9
            )
            axes[3, station_index].set_ylabel("degree", fontsize=8)

            axes[4, station_index].plot(
                date,
                frame["interval_level"],
                color="#4a3aa7",
                linewidth=1.4,
            )
            axes[4, station_index].set_yticks(
                range(5), ["G", "B", "Y", "O", "R"]
            )
            axes[4, station_index].set_title(
                f"{station} · raw interval state", loc="left", fontsize=9
            )

            status_text = (
                "automatic V0: "
                f"{str(candidate['v0_candidate_status']).replace('_', ' ')}\n"
                "candidate branch: available (display only)"
            )
            axes[0, station_index].text(
                0.02,
                0.94,
                status_text,
                transform=axes[0, station_index].transAxes,
                va="top",
                fontsize=7.5,
                bbox={
                    "facecolor": "white",
                    "edgecolor": "#c3c2b7",
                    "alpha": 0.9,
                },
            )

        for metric_index in range(5):
            for station_index in range(2):
                axis = axes[metric_index, station_index]
                axis.grid(alpha=0.2, linewidth=0.5)
                axis.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
                axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
                axis.tick_params(axis="y", labelsize=7)
                if metric_index < 4:
                    axis.tick_params(axis="x", labelbottom=False)
                else:
                    axis.tick_params(axis="x", labelrotation=30, labelsize=7)

        unavailable = summary.loc[
            summary["v0_candidate_status"].eq("unavailable"),
            ["station", "v0_candidate_status", "v5_kinematic_branch_status"],
        ].copy()
        unavailable["v5_kinematic_branch_status"] = (
            "not applicable (V0 unavailable)"
        )
        status_axis = fig.add_subplot(grid[5, :])
        status_axis.axis("off")
        status_axis.set_title(
            "Automatic V0 unavailable: retained explicitly without fallback",
            loc="left",
            fontsize=9,
            fontweight="bold",
            pad=5,
        )
        table = status_axis.table(
            cellText=unavailable.astype(str).values.tolist(),
            colLabels=["station", "automatic V0", "v5 kinematic branch"],
            cellLoc="left",
            colLoc="left",
            loc="center",
            bbox=[0.08, 0.02, 0.84, 0.88],
        )
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        for (row_index, _), cell in table.get_celld().items():
            cell.set_edgecolor("#d7d6ce")
            cell.set_linewidth(0.6)
            cell.set_facecolor("#f1f1ec" if row_index == 0 else "white")

        fig.suptitle(
            "V5 candidate input display: automatic V0 availability "
            "(no NGBoost inference or fusion output)",
            fontsize=13,
            fontweight="bold",
            y=0.992,
        )
        fig.text(
            0.5,
            0.008,
            "MJ1/MJ3 expose the four candidate inputs. Purple is the raw "
            "interval-state scale, not a new warning color; v4 remains unchanged.",
            ha="center",
            fontsize=8.5,
        )
        fig.subplots_adjust(top=0.96, bottom=0.035, left=0.09, right=0.98)
        output_png.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(
            output_png,
            dpi=300,
            facecolor="white",
            metadata={"Software": "Landslide-Warning"},
        )
        fig.savefig(output_svg, facecolor="white", metadata={"Date": None})
        svg = output_svg.read_text(encoding="utf-8")
        output_svg.write_text(
            "\n".join(line.rstrip() for line in svg.splitlines()) + "\n",
            encoding="utf-8",
        )
        plt.close(fig)


def _source_record(path: Path) -> dict[str, Any]:
    return {
        "path": _manifest_path(path),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _output_record(
    staged_path: Path,
    target_path: Path,
    *,
    rows: int | None = None,
) -> dict[str, Any]:
    record = {
        "path": _manifest_path(target_path),
        "sha256": _sha256_file(staged_path),
        "size_bytes": staged_path.stat().st_size,
    }
    if rows is not None:
        record["n_rows"] = rows
    return record


def write_candidate_display(
    *,
    profile_path: Path = DEFAULT_PROFILE_PATH,
    v0_candidates_path: Path = DEFAULT_V0_CANDIDATES_PATH,
    v0_segments_path: Path = DEFAULT_V0_SEGMENTS_PATH,
    v0_manifest_path: Path = DEFAULT_V0_MANIFEST_PATH,
    kinematics_path: Path = DEFAULT_KINEMATICS_PATH,
    predictions_path: Path = DEFAULT_PREDICTIONS_PATH,
    forecast_manifest_path: Path = DEFAULT_FORECAST_MANIFEST_PATH,
    v4_station_path: Path = DEFAULT_V4_STATION_PATH,
    v4_site_path: Path = DEFAULT_V4_SITE_PATH,
    v4_manifest_path: Path = DEFAULT_V4_MANIFEST_PATH,
    thresholds_path: Path = DEFAULT_THRESHOLDS_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> Path:
    """Write the display-only v5 candidate bundle."""
    profile_path = Path(profile_path).resolve()
    v0_candidates_path = Path(v0_candidates_path).resolve()
    v0_segments_path = Path(v0_segments_path).resolve()
    v0_manifest_path = Path(v0_manifest_path).resolve()
    kinematics_path = Path(kinematics_path).resolve()
    predictions_path = Path(predictions_path).resolve()
    forecast_manifest_path = Path(forecast_manifest_path).resolve()
    v4_station_path = Path(v4_station_path).resolve()
    v4_site_path = Path(v4_site_path).resolve()
    v4_manifest_path = Path(v4_manifest_path).resolve()
    thresholds_path = Path(thresholds_path).resolve()
    output_dir = Path(output_dir).resolve()
    _validate_output_dir(output_dir)
    profile = load_candidate_display_profile(profile_path)
    v0_manifest = _validate_v0_manifest(
        v0_manifest_path,
        v0_candidates_path,
        v0_segments_path,
    )
    forecast_source = base.validate_forecast_lineage(
        predictions_path, forecast_manifest_path
    )
    v4_source = base.validate_v4_lineage(
        kinematics_path=kinematics_path,
        predictions_path=predictions_path,
        thresholds_path=thresholds_path,
        manifest_path=v4_manifest_path,
    )
    v4_source["reference_outputs"] = _validate_v4_reference_lineage(
        v4_manifest_path,
        v4_station_path,
        v4_site_path,
    )
    candidates = _validate_v0_candidates(pd.read_csv(v0_candidates_path))
    _validate_v0_segments(pd.read_csv(v0_segments_path))
    kinematics = pd.read_csv(
        kinematics_path,
        usecols=["case", "date", "station", "velocity", "velocity_status", "delta_v", "delta_v_status"],
    )
    kinematics["date"] = pd.to_datetime(kinematics["date"], errors="coerce")
    if not kinematics["case"].astype("string").eq("ootang").all():
        raise CandidateDisplayInputError("Candidate display kinematics must be Ootang only")
    predictions = pd.read_csv(predictions_path, usecols=["date", "station", "split", "actual", "p10", "p50", "p90"])
    v4_station = pd.read_csv(v4_station_path, usecols=["date", "station", "final_color", "candidate_color"])
    v4_site = pd.read_csv(
        v4_site_path,
        usecols=[
            "date",
            "site_fusion_status",
            "site_confirmed_color",
            "local_max_candidate_color",
        ],
    )
    timeline, summary = build_candidate_display_timeline(
        profile=profile,
        v0_candidates=candidates,
        kinematics=kinematics,
        predictions=predictions,
        v4_station=v4_station,
        v4_site=v4_site,
    )
    with tempfile.TemporaryDirectory(prefix=".ootang-v5-candidate-display-", dir=ROOT) as directory:
        staging = Path(directory)
        staged_timeline = staging / "candidate_timeline.csv"
        staged_summary = staging / "candidate_summary.csv"
        staged_png = staging / "candidate_display.png"
        staged_svg = staging / "candidate_display.svg"
        timeline.to_csv(staged_timeline, index=False, lineterminator="\n")
        summary.to_csv(staged_summary, index=False, lineterminator="\n")
        _plot_candidate_display(timeline, summary, staged_png, staged_svg)
        targets = {
            "timeline": output_dir / "candidate_timeline.csv",
            "summary": output_dir / "candidate_summary.csv",
            "figure_png": output_dir / "candidate_display.png",
            "figure_svg": output_dir / "candidate_display.svg",
            "manifest": output_dir / "manifest.json",
        }
        output_records = {
            "timeline": _output_record(
                staged_timeline, targets["timeline"], rows=len(timeline)
            ),
            "summary": _output_record(
                staged_summary, targets["summary"], rows=len(summary)
            ),
            "figure_png": _output_record(staged_png, targets["figure_png"]),
            "figure_svg": _output_record(staged_svg, targets["figure_svg"]),
        }
        manifest = {
            "schema_version": 1,
            "artifact_kind": ARTIFACT_KIND,
            "artifact_status": ARTIFACT_STATUS,
            "case": "ootang",
            "formal_warning_output": False,
            "candidate_display_only": True,
            "candidate_warning_color_output": False,
            "ngboost_inference_output": False,
            "v5_fusion_output": False,
            "vajont_used": False,
            "default_pipeline_member": False,
            "git_commit": base._git_commit(),
            "git_worktree_dirty": base._git_worktree_dirty(),
            "implementation_sources": {
                "runner": _source_record(Path(__file__).resolve()),
                "automatic_v0": _source_record(Path(auto_v0.__file__).resolve()),
                "lineage_helpers": _source_record(Path(base.__file__).resolve()),
                "interval_state": _source_record(
                    ROOT / "code" / "warning" / "interval_state.py"
                ),
                "artifact_promotion": _source_record(
                    ROOT / "code" / "warning" / "draft_evidence.py"
                ),
            },
            "profile": {
                "id": profile["profile_id"],
                "version": profile["profile_version"],
                "path": _manifest_path(profile_path),
                "file_sha256": _sha256_file(profile_path),
                "content_sha256": _canonical_json_sha256(profile),
            },
            "source_inputs": {
                "v0_candidates": {
                    "path": _manifest_path(v0_candidates_path),
                    "sha256": _sha256_file(v0_candidates_path),
                },
                "v0_segments": {
                    "path": _manifest_path(v0_segments_path),
                    "sha256": _sha256_file(v0_segments_path),
                },
                "v0_manifest": v0_manifest,
                "kinematics": {
                    "path": _manifest_path(kinematics_path),
                    "sha256": _sha256_file(kinematics_path),
                },
                "predictions": {
                    "path": _manifest_path(predictions_path),
                    "sha256": _sha256_file(predictions_path),
                },
                "forecast_manifest": forecast_source,
                "v4_station": {
                    "path": _manifest_path(v4_station_path),
                    "sha256": _sha256_file(v4_station_path),
                },
                "v4_site": {
                    "path": _manifest_path(v4_site_path),
                    "sha256": _sha256_file(v4_site_path),
                },
                "v4_manifest": v4_source,
            },
            "result_splits": ["calibration", "test"],
            "status_counts": {
                str(name): int(count)
                for name, count in candidates["selection_status"]
                .value_counts()
                .items()
            },
            "branch_status_counts": {
                str(name): int(count)
                for name, count in timeline["v5_kinematic_branch_status"]
                .value_counts()
                .items()
            },
            "timeline_rows": len(timeline),
            "timeline_dates": int(timeline["date"].nunique()),
            "summary_rows": len(summary),
            "v0_available_rows": int(timeline["v0_candidate_available"].sum()),
            "v0_not_applicable_rows": int(
                (~timeline["v0_candidate_available"]).sum()
            ),
            "v0_available_stations": candidates.loc[
                candidates["selection_status"].isin(V0_AVAILABLE_STATUSES),
                "station",
            ].tolist(),
            "v0_unavailable_stations": candidates.loc[
                candidates["selection_status"].eq("unavailable"), "station"
            ].tolist(),
            "outputs": output_records,
            "not_claimed": profile["not_claimed"],
        }
        staged_manifest = staging / "manifest.json"
        staged_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        replacements = (
            FileReplacement(staged_timeline, targets["timeline"]),
            FileReplacement(staged_summary, targets["summary"]),
            FileReplacement(staged_png, targets["figure_png"]),
            FileReplacement(staged_svg, targets["figure_svg"]),
            FileReplacement(staged_manifest, targets["manifest"]),
        )
        promote_staged_files(replacements)
    return targets["manifest"]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Ootang display-only v5 candidate branch.")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH)
    parser.add_argument("--v0-candidates", type=Path, default=DEFAULT_V0_CANDIDATES_PATH)
    parser.add_argument("--v0-segments", type=Path, default=DEFAULT_V0_SEGMENTS_PATH)
    parser.add_argument("--v0-manifest", type=Path, default=DEFAULT_V0_MANIFEST_PATH)
    parser.add_argument("--kinematics", type=Path, default=DEFAULT_KINEMATICS_PATH)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS_PATH)
    parser.add_argument("--forecast-manifest", type=Path, default=DEFAULT_FORECAST_MANIFEST_PATH)
    parser.add_argument("--v4-station", type=Path, default=DEFAULT_V4_STATION_PATH)
    parser.add_argument("--v4-site", type=Path, default=DEFAULT_V4_SITE_PATH)
    parser.add_argument("--v4-manifest", type=Path, default=DEFAULT_V4_MANIFEST_PATH)
    parser.add_argument("--thresholds", type=Path, default=DEFAULT_THRESHOLDS_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    manifest = write_candidate_display(
        profile_path=args.profile,
        v0_candidates_path=args.v0_candidates,
        v0_segments_path=args.v0_segments,
        v0_manifest_path=args.v0_manifest,
        kinematics_path=args.kinematics,
        predictions_path=args.predictions,
        forecast_manifest_path=args.forecast_manifest,
        v4_station_path=args.v4_station,
        v4_site_path=args.v4_site,
        v4_manifest_path=args.v4_manifest,
        thresholds_path=args.thresholds,
    )
    print(f"[v5-candidate-display] Ootang display bundle: {manifest}")


if __name__ == "__main__":
    main()


__all__ = [
    "ARTIFACT_KIND",
    "ARTIFACT_STATUS",
    "CandidateDisplayInputError",
    "CandidateDisplayOutputError",
    "CandidateDisplayProfileError",
    "DEFAULT_OUTPUT_DIR",
    "SUMMARY_COLUMNS",
    "TIMELINE_COLUMNS",
    "build_candidate_display_timeline",
    "load_candidate_display_profile",
    "write_candidate_display",
]
