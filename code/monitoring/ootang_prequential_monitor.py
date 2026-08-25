"""Causal retrospective replay for the Ootang machine-only monitor.

The runner consumes the five-seed, three-fold ConvLSTM OOF predictions.  It
issues every station forecast for a target date before revealing any outcome
from that date.  Outcomes may update only later dates.  The resulting anomaly
scores are continuous research-monitoring quantities, not event labels or a
formal alerting product.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

import numpy as np
import pandas as pd


CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.draft_evidence import (  # noqa: E402
    FileReplacement,
    promote_staged_files,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_prequential_monitor.v1.json"
DEFAULT_PREDICTIONS_PATH = (
    ROOT
    / "figures"
    / "convlstm"
    / "runs"
    / "displacement_elevation_exog_v1"
    / "fixed120_v1"
    / "seed_stability_0_4"
    / "seed_stability_predictions.csv"
)
DEFAULT_SOURCE_MANIFEST_PATH = DEFAULT_PREDICTIONS_PATH.parent / "manifest.json"
DEFAULT_OUTPUT_DIR = ROOT / "figures" / "prequential_anomaly_ootang_v1"

ARTIFACT_KIND = "ootang_prequential_anomaly_monitor"
ARTIFACT_STATUS = "retrospective_prequential_self_supervised_not_confirmatory"
ZERO_HASH = "0" * 64
FORBIDDEN_OUTPUT_FIELDS = frozenset(
    {"warning_color", "field_truth", "event_recall", "far"}
)

IDENTITY_COLUMNS = ("fold", "date", "station")
ISSUE_COLUMNS = (
    *IDENTITY_COLUMNS,
    "issue_date_sequence",
    "issue_state_reset_reason",
    "issue_state_before_sha256",
    "issue_history_count",
    "issue_forecast_action",
    "issue_uncertainty_action",
    "issue_eta",
    "issue_expert_persistence_mm",
    "issue_expert_seed0_p50_mm",
    "issue_expert_seed1_p50_mm",
    "issue_expert_seed2_p50_mm",
    "issue_expert_seed3_p50_mm",
    "issue_expert_seed4_p50_mm",
    "issue_weight_persistence",
    "issue_weight_seed0",
    "issue_weight_seed1",
    "issue_weight_seed2",
    "issue_weight_seed3",
    "issue_weight_seed4",
    "issue_point_forecast_mm",
    "issue_fallback_persistence_mm",
    "issue_aci_alpha",
    "issue_conformal_history_count",
    "issue_conformal_q_mm",
    "issue_interval_lower_mm",
    "issue_interval_upper_mm",
)
HASH_COLUMNS = ("previous_issue_batch_sha256", "issue_batch_sha256")
REVEAL_COLUMNS = (
    "reveal_actual_mm",
    "reveal_point_absolute_error_mm",
    "reveal_persistence_absolute_error_mm",
    "reveal_interval_covered",
    "reveal_interval_width_mm",
    "reveal_underprediction_residual_mm",
    "reveal_anomaly_p_value",
    "reveal_anomaly_score",
    "reveal_absolute_residual_surprise_rank",
    "reveal_drift_detected",
    "reveal_drift_cut_index",
    "reveal_aci_alpha_after_update",
    "reveal_history_count_after_update",
    "reveal_state_reset_after_update",
    "reveal_updated_state_sha256",
    "reveal_state_after_sha256",
    "reveal_actual_used_for_future_update",
    "reveal_phase",
)
FLAG_COLUMNS = (
    "artifact_status",
    "formal_warning_output",
    "independent_label_used",
    "confirmatory_external_validation",
    "vajont_used",
)
STATION_TIMELINE_COLUMNS = (
    *ISSUE_COLUMNS,
    *HASH_COLUMNS,
    *REVEAL_COLUMNS,
    *FLAG_COLUMNS,
)
SITE_TIMELINE_COLUMNS = (
    "fold",
    "date",
    "issue_batch_sha256",
    "outcome_phase",
    "available_station_score_count",
    "abstained_station_score_count",
    "block_o1_available_station_count",
    "block_o1_expected_station_count",
    "block_o1_contributor_station",
    "block_o1_max_anomaly_score",
    "block_o2_available_station_count",
    "block_o2_expected_station_count",
    "block_o2_contributor_station",
    "block_o2_max_anomaly_score",
    "block_o3_available_station_count",
    "block_o3_expected_station_count",
    "block_o3_contributor_station",
    "block_o3_max_anomaly_score",
    "cross_block_min_of_block_max_anomaly_score",
    "site_score_status",
    *FLAG_COLUMNS,
)
METRIC_COLUMNS = (
    "fold",
    "scope",
    "station",
    "rows",
    "point_metric_population",
    "point_rmse_mm",
    "point_mae_mm",
    "persistence_rmse_mm",
    "persistence_mae_mm",
    "point_rmse_skill_vs_persistence",
    "point_mae_skill_vs_persistence",
    "active_point_rows",
    "active_point_rmse_mm",
    "active_point_mae_mm",
    "active_persistence_rmse_mm",
    "active_persistence_mae_mm",
    "active_point_rmse_skill_vs_persistence",
    "active_point_mae_skill_vs_persistence",
    "interval_rows",
    "empirical_coverage",
    "coverage_gap",
    "mean_interval_width_mm",
    "mean_interval_score_80_mm",
    "abstention_rows",
    "abstention_rate",
    "drift_count",
    "anomaly_rows",
    "mean_anomaly_score",
    "p95_anomaly_score",
    "maximum_anomaly_score",
    *FLAG_COLUMNS,
)


class PrequentialConfigError(ValueError):
    """Raised when the versioned machine-monitor config is malformed."""


class PrequentialInputError(RuntimeError):
    """Raised before live output when an OOF source violates its contract."""


class PrequentialOutputError(RuntimeError):
    """Raised when an in-memory result violates the output contract."""


@dataclass(frozen=True)
class ValidatedSource:
    """A fully checked and canonically ordered OOF source."""

    frame: pd.DataFrame
    predictions_sha256: str
    predictions_canonical_sha256: str
    predictions_size_bytes: int
    source_manifest_sha256: str
    declared_output: dict[str, Any]
    persistence_reference_commit: str
    persistence_reference_path: str
    persistence_reference_sha256: str
    persistence_reference_size_bytes: int
    persistence_reference_actual_rows: int
    persistence_reference_lag1_rows: int
    persistence_reference_first_prior_date: str


@dataclass(frozen=True)
class _ValidatedPersistenceReference:
    """A source-manifest-bound historical feature blob and alignment audit."""

    commit: str
    path: str
    sha256: str
    size_bytes: int
    frame: pd.DataFrame


@dataclass
class _StationState:
    """Online state belonging to exactly one station and model-version fold."""

    expert_count: int
    initial_alpha: float
    cumulative_loss: np.ndarray = field(init=False)
    history_count: int = 0
    absolute_residuals: list[float] = field(default_factory=list)
    underprediction_residuals: list[float] = field(default_factory=list)
    drift_values: list[float] = field(default_factory=list)
    alpha: float = field(init=False)
    next_issue_reset_reason: str = "fold_start"

    def __post_init__(self) -> None:
        self.cumulative_loss = np.zeros(self.expert_count, dtype=float)
        self.alpha = self.initial_alpha


def _reject_json_constant(value: str) -> None:
    raise PrequentialConfigError(f"Forbidden JSON constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PrequentialConfigError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path, *, config: bool, name: str) -> dict[str, Any]:
    error_type = PrequentialConfigError if config else PrequentialInputError
    try:
        text = Path(path).read_text(encoding="utf-8")
        payload = json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except FileNotFoundError as exc:
        raise error_type(f"Missing {name}: {path}") from exc
    except (OSError, json.JSONDecodeError, PrequentialConfigError) as exc:
        raise error_type(f"Invalid {name}: {path}") from exc
    if not isinstance(payload, dict):
        raise error_type(f"{name} must be a JSON object")
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1_048_576), b""):
                digest.update(chunk)
    except (FileNotFoundError, OSError) as exc:
        raise PrequentialInputError(f"Cannot hash source file: {path}") from exc
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_path(path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _require_equal(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise PrequentialConfigError(
            f"{name} must be {expected!r}; found {actual!r}"
        )


def _expected_profile() -> dict[str, Any]:
    """Return the exact v1 schema and semantics implemented by this runner."""

    stations = ["ATU1", "ATU2", "ATU3", "ATU4", "ATU5", "MJ1", "MJ3", "MJ9"]
    return {
        "profile_id": "ootang-prequential-monitor-v1",
        "profile_version": "1.0.0",
        "artifact_kind": ARTIFACT_KIND,
        "artifact_status": ARTIFACT_STATUS,
        "case": "ootang",
        "default_pipeline_member": False,
        "formal_warning_output": False,
        "independent_label_used": False,
        "confirmatory_external_validation": False,
        "vajont_used": False,
        "source_contract": {
            "predictions": (
                "figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/"
                "seed_stability_0_4/seed_stability_predictions.csv"
            ),
            "manifest": (
                "figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/"
                "seed_stability_0_4/manifest.json"
            ),
            "manifest_schema_version": "ootang_convlstm_stage_bundle_manifest_v1",
            "manifest_stage": "convlstm-seeds",
            "manifest_status": "completed",
            "evidence_scope": "internal_exploratory_materialized_daily_series",
            "required_columns": [
                "seed",
                "fold",
                "model_input_schema",
                "model_input_channels",
                "station_geometry_sha256",
                "date",
                "station",
                "actual",
                "persistence",
                "raw_p10",
                "p50",
                "raw_p90",
                "calibrated_p10",
                "calibrated_p90",
                "qhat_mm",
            ],
            "model_input_schema": "displacement_elevation_exog_v1",
            "model_input_channels": 7,
            "seeds": [0, 1, 2, 3, 4],
            "folds": [1, 2, 3],
            "dates_per_fold": 287,
            "stations": stations,
            "same_seed_key_fields": ["fold", "date", "station"],
            "same_seed_value_fields": [
                "actual",
                "persistence",
                "model_input_schema",
                "model_input_channels",
                "station_geometry_sha256",
            ],
            "quantile_orders": [
                ["raw_p10", "p50", "raw_p90"],
                ["calibrated_p10", "p50", "calibrated_p90"],
            ],
            "persistence_reference": {
                "retrieval": "git_blob_at_source_manifest_commit",
                "manifest_commit_field": "git.commit",
                "manifest_input_path": "data/features.csv",
                "date_column": "Date",
                "station_displacement_columns": {
                    station: f"{station}_disp" for station in stations
                },
                "actual_rule": "target_natural_day_station_displacement",
                "persistence_rule": "previous_natural_day_station_displacement",
                "first_target_policy": "require_previous_natural_day_reference",
                "comparison": "exact_binary64_after_round_trip_csv_parse",
            },
        },
        "algorithm": {
            "expert_columns": [
                "persistence",
                "seed0_p50",
                "seed1_p50",
                "seed2_p50",
                "seed3_p50",
                "seed4_p50",
            ],
            "expert_loss": (
                "absolute_error_divided_by_same_station_date_max_expert_absolute_error"
            ),
            "expert_weight_rule": (
                "exp_negative_eta_t_times_cumulative_past_normalized_loss"
            ),
            "eta_rule": "sqrt_8_log_k_div_t",
            "minimum_history": 60,
            "maximum_history": 180,
            "target_coverage": 0.8,
            "conformal_residual": "absolute_actual_minus_issue_point",
            "conformal_interval": "symmetric_two_sided",
            "conformal_quantile": "finite_sample_higher",
            "aci": {
                "initial_alpha": 0.2,
                "gamma": 1.0 / 180.0,
                "minimum_alpha": 0.01,
                "maximum_alpha": 0.5,
                "update_phase": "after_outcome_reveal",
            },
            "anomaly": {
                "residual": "max_actual_minus_issue_point_zero",
                "p_value": (
                    "one_plus_past_greater_equal_divided_by_one_plus_past_count"
                ),
                "score": "negative_log10_p_value",
            },
            "drift": {
                "detector": "adwin_inspired_bounded_hoeffding_adaptive_window",
                "input": "absolute_residual_empirical_surprise_rank",
                "delta": 0.002,
                "minimum_subwindow": 30,
                "maximum_history": 180,
                "reset_timing": "after_reveal_so_next_date_rewarms",
            },
            "fold_change_policy": (
                "reset_all_station_online_state_before_first_issue_of_new_fold"
            ),
            "same_date_policy": (
                "issue_all_eight_stations_then_reveal_all_eight_outcomes"
            ),
            "rewarm_policy": {
                "point_forecast": (
                    "online_weighted_ensemble_from_issue_time_past_only"
                ),
                "fallback_shadow": "persistence",
                "forecast_action": "abstain",
                "interval": "unavailable",
            },
            "spatial_blocks": {
                "O1": ["MJ9", "MJ1", "MJ3"],
                "O2": ["ATU4", "ATU5", "ATU3"],
                "O3": ["ATU2", "ATU1"],
            },
            "site_score": "minimum_of_the_three_block_maximum_anomaly_scores",
        },
        "outputs": {
            "directory": "figures/prequential_anomaly_ootang_v1",
            "station_timeline": "station_timeline.csv",
            "site_timeline": "site_timeline.csv",
            "metrics": "prequential_metrics.csv",
            "manifest": "manifest.json",
        },
        "replay_contract": {
            "mode": "retrospective_replay_not_realtime_sealing",
            "issue_batch_hash": {
                "kind": "canonical_issue_only_sha256_chain",
                "algorithm": "sha256",
                "columns": "runner_issue_columns_v1_in_declared_order",
                "row_order": "source_contract_station_order",
                "float_serialization": "binary64_round_trip_percent_dot_17g",
                "null_serialization": "json_null",
                "json_serialization": "utf8_sort_keys_compact_no_nan",
                "payload": "previous_hash_newline_then_canonical_issue_json",
            },
            "outcome_use": (
                "actual_is_used_only_after_all_same_date_issues_for_reveal_and_future_updates"
            ),
        },
    }


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load and exact-match the complete versioned algorithm contract."""

    profile = _load_json(Path(path), config=True, name="prequential config")
    _require_equal(profile, _expected_profile(), "prequential config")
    return profile


def _find_prediction_record(
    manifest: dict[str, Any], profile: dict[str, Any]
) -> dict[str, Any]:
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list):
        raise PrequentialInputError("Source manifest outputs must be a list")
    expected_name = Path(profile["source_contract"]["predictions"]).name
    expected_path = profile["source_contract"]["predictions"]
    matches = [
        record
        for record in outputs
        if isinstance(record, dict)
        and Path(str(record.get("path", ""))).name == expected_name
        and record.get("path") == expected_path
    ]
    if len(matches) != 1:
        raise PrequentialInputError(
            "Source manifest must declare exactly one seed-stability predictions payload"
        )
    return matches[0]


def _find_manifest_input_record(
    manifest: dict[str, Any], expected_path: str
) -> dict[str, Any]:
    inputs = manifest.get("inputs")
    if not isinstance(inputs, list):
        raise PrequentialInputError("Source manifest inputs must be a list")
    matches = [
        record
        for record in inputs
        if isinstance(record, dict) and record.get("path") == expected_path
    ]
    if len(matches) != 1:
        raise PrequentialInputError(
            f"Source manifest must declare exactly one input {expected_path}"
        )
    return matches[0]


def _git_blob_bytes(commit: str, path: str) -> bytes:
    """Read an exact historical blob; absent/shallow Git history fails closed."""

    if (
        len(commit) != 40
        or any(character not in "0123456789abcdef" for character in commit)
    ):
        raise PrequentialInputError(
            "Source manifest git.commit must be a full lowercase 40-hex commit"
        )
    try:
        object_type = subprocess.run(
            ["git", "cat-file", "-t", commit],
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise PrequentialInputError("Cannot execute git for source reference") from exc
    if object_type.returncode != 0 or object_type.stdout.strip() != b"commit":
        raise PrequentialInputError(
            "Source manifest commit is unavailable or is not a commit object"
        )
    result = subprocess.run(
        ["git", "cat-file", "blob", f"{commit}:{path}"],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise PrequentialInputError(
            f"Historical persistence reference blob is unavailable: {commit}:{path}"
        )
    return result.stdout


def _load_persistence_reference(
    manifest: dict[str, Any], source: dict[str, Any]
) -> _ValidatedPersistenceReference:
    reference = source["persistence_reference"]
    git_metadata = manifest.get("git")
    if not isinstance(git_metadata, dict):
        raise PrequentialInputError("Source manifest git metadata is missing")
    commit = git_metadata.get("commit")
    if not isinstance(commit, str):
        raise PrequentialInputError("Source manifest git.commit is missing")
    if git_metadata.get("tracked_worktree_dirty") is not False:
        raise PrequentialInputError(
            "Source manifest must declare a clean tracked worktree"
        )
    path = reference["manifest_input_path"]
    record = _find_manifest_input_record(manifest, path)
    blob = _git_blob_bytes(commit, path)
    blob_sha256 = _sha256_bytes(blob)
    blob_size = len(blob)
    if record.get("sha256") != blob_sha256:
        raise PrequentialInputError(
            "Historical features blob SHA-256 does not match source manifest input"
        )
    if record.get("size_bytes") != blob_size:
        raise PrequentialInputError(
            "Historical features blob size does not match source manifest input"
        )
    try:
        features = pd.read_csv(io.BytesIO(blob), float_precision="round_trip")
    except (pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise PrequentialInputError(
            "Cannot parse historical persistence reference CSV"
        ) from exc
    date_column = reference["date_column"]
    required = [date_column, *reference["station_displacement_columns"].values()]
    missing = [column for column in required if column not in features.columns]
    if missing:
        raise PrequentialInputError(
            f"Historical persistence reference lacks columns: {missing}"
        )
    original_dates = features[date_column].copy()
    try:
        parsed_dates = pd.to_datetime(
            original_dates, format="%Y-%m-%d", errors="raise"
        )
    except (TypeError, ValueError) as exc:
        raise PrequentialInputError(
            "Historical persistence reference dates are invalid"
        ) from exc
    if not (parsed_dates.dt.strftime("%Y-%m-%d") == original_dates).all():
        raise PrequentialInputError(
            "Historical persistence reference dates are not canonical YYYY-MM-DD"
        )
    if parsed_dates.duplicated().any():
        raise PrequentialInputError(
            "Historical persistence reference dates are not unique"
        )
    if not pd.DatetimeIndex(parsed_dates).equals(
        pd.date_range(parsed_dates.iloc[0], parsed_dates.iloc[-1], freq="D")
    ):
        raise PrequentialInputError(
            "Historical persistence reference dates are not daily and contiguous"
        )
    features[date_column] = parsed_dates
    for column in reference["station_displacement_columns"].values():
        numeric = pd.to_numeric(features[column], errors="coerce")
        if not np.isfinite(numeric.to_numpy(dtype=float)).all():
            raise PrequentialInputError(
                f"Historical persistence reference column is not finite: {column}"
            )
        features[column] = numeric
    return _ValidatedPersistenceReference(
        commit=commit,
        path=path,
        sha256=blob_sha256,
        size_bytes=blob_size,
        frame=features,
    )


def _validate_actual_and_persistence_alignment(
    prediction_frame: pd.DataFrame,
    reference: _ValidatedPersistenceReference,
    source: dict[str, Any],
) -> tuple[int, int, str]:
    """Bind every OOF target and persistence value to the historical blob."""

    contract = source["persistence_reference"]
    target = prediction_frame.loc[
        prediction_frame["seed"] == source["seeds"][0],
        ["fold", "date", "station", "actual", "persistence"],
    ].copy()
    parts: list[pd.DataFrame] = []
    date_column = contract["date_column"]
    for station in source["stations"]:
        displacement_column = contract["station_displacement_columns"][station]
        values = reference.frame[[date_column, displacement_column]].rename(
            columns={date_column: "reference_date", displacement_column: "value"}
        )
        actual = values.rename(
            columns={"reference_date": "date", "value": "expected_actual"}
        )
        actual["station"] = station
        persistence = values.rename(columns={"value": "expected_persistence"})
        persistence["date"] = persistence["reference_date"] + pd.Timedelta(days=1)
        persistence = persistence.drop(columns="reference_date")
        persistence["station"] = station
        parts.append(
            actual.merge(
                persistence,
                on=["date", "station"],
                how="inner",
                validate="one_to_one",
            )
        )
    expected = pd.concat(parts, ignore_index=True)
    merged = target.merge(
        expected,
        on=["date", "station"],
        how="left",
        validate="one_to_one",
    )
    if merged[["expected_actual", "expected_persistence"]].isna().any().any():
        raise PrequentialInputError(
            "An OOF target lacks same-day or previous-natural-day reference"
        )
    actual_equal = (
        merged["actual"].to_numpy(dtype=float)
        == merged["expected_actual"].to_numpy(dtype=float)
    )
    persistence_equal = (
        merged["persistence"].to_numpy(dtype=float)
        == merged["expected_persistence"].to_numpy(dtype=float)
    )
    if not actual_equal.all():
        raise PrequentialInputError(
            f"OOF actual disagrees with historical target-day displacement in "
            f"{int((~actual_equal).sum())} rows"
        )
    if not persistence_equal.all():
        raise PrequentialInputError(
            f"OOF persistence is not previous-natural-day displacement in "
            f"{int((~persistence_equal).sum())} rows"
        )
    first_prior_date = (target["date"].min() - pd.Timedelta(days=1)).strftime(
        "%Y-%m-%d"
    )
    return len(target), len(target), first_prior_date


def _canonical_frame_sha256(frame: pd.DataFrame) -> str:
    ordered = frame.sort_values(["seed", "fold", "date", "station"]).copy()
    ordered["date"] = pd.to_datetime(ordered["date"]).dt.strftime("%Y-%m-%d")
    text = ordered.to_csv(index=False, lineterminator="\n", float_format="%.17g")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_source(
    predictions_path: Path,
    source_manifest_path: Path,
    profile: dict[str, Any],
) -> ValidatedSource:
    """Fail closed on the complete OOF source before any live output exists."""

    predictions_path = Path(predictions_path).resolve()
    source_manifest_path = Path(source_manifest_path).resolve()
    manifest = _load_json(
        source_manifest_path, config=False, name="ConvLSTM seed-stability manifest"
    )
    source = profile["source_contract"]
    required_manifest = {
        "schema_version": source["manifest_schema_version"],
        "stage": source["manifest_stage"],
        "status": source["manifest_status"],
        "evidence_scope": source["evidence_scope"],
        "formal_warning_output": False,
        "confirmatory_external_validation": False,
    }
    for name, expected in required_manifest.items():
        if manifest.get(name) != expected:
            raise PrequentialInputError(
                f"Source manifest {name} changed: expected {expected!r}"
            )
    parameters = manifest.get("parameters")
    if not isinstance(parameters, dict):
        raise PrequentialInputError("Source manifest parameters are missing")
    expected_parameters = {
        "seeds": source["seeds"],
        "fold_count": len(source["folds"]),
        "run_count": len(source["seeds"]) * len(source["folds"]),
        "save_all_predictions": True,
        "best_seed_selected": False,
        "test_used_for_selection": False,
    }
    for name, expected in expected_parameters.items():
        if parameters.get(name) != expected:
            raise PrequentialInputError(
                f"Source manifest parameter {name} changed"
            )
    persistence_reference = _load_persistence_reference(manifest, source)

    record = _find_prediction_record(manifest, profile)
    expected_rows = (
        len(source["seeds"])
        * len(source["folds"])
        * source["dates_per_fold"]
        * len(source["stations"])
    )
    if record.get("columns") != source["required_columns"]:
        raise PrequentialInputError("Source manifest prediction schema changed")
    if record.get("rows") != expected_rows:
        raise PrequentialInputError("Source manifest prediction row count changed")
    actual_sha = _sha256_file(predictions_path)
    if record.get("sha256") != actual_sha:
        raise PrequentialInputError(
            "Predictions SHA-256 does not match its source manifest"
        )
    try:
        size_bytes = predictions_path.stat().st_size
    except OSError as exc:
        raise PrequentialInputError(
            f"Cannot stat predictions: {predictions_path}"
        ) from exc
    if record.get("size_bytes") != size_bytes:
        raise PrequentialInputError("Predictions size does not match source manifest")

    try:
        frame = pd.read_csv(predictions_path)
    except (OSError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise PrequentialInputError(f"Cannot read predictions: {predictions_path}") from exc
    if list(frame.columns) != source["required_columns"]:
        raise PrequentialInputError("Prediction CSV schema changed")
    if len(frame) != expected_rows:
        raise PrequentialInputError(
            f"Prediction CSV must have {expected_rows} rows; found {len(frame)}"
        )

    numeric_columns = [
        column
        for column in source["required_columns"]
        if column
        not in {"model_input_schema", "station_geometry_sha256", "date", "station"}
    ]
    for column in numeric_columns:
        converted = pd.to_numeric(frame[column], errors="coerce")
        if not np.isfinite(converted.to_numpy(dtype=float)).all():
            raise PrequentialInputError(f"Prediction column {column} is not fully finite")
        frame[column] = converted
    for column in ("seed", "fold", "model_input_channels"):
        values = frame[column].to_numpy(dtype=float)
        if not np.equal(values, np.floor(values)).all():
            raise PrequentialInputError(f"Prediction column {column} must be integral")
        frame[column] = frame[column].astype(int)

    try:
        parsed_dates = pd.to_datetime(frame["date"], format="%Y-%m-%d", errors="raise")
    except (ValueError, TypeError) as exc:
        raise PrequentialInputError("Prediction dates must be valid YYYY-MM-DD dates") from exc
    if not (parsed_dates.dt.strftime("%Y-%m-%d") == frame["date"]).all():
        raise PrequentialInputError("Prediction dates must use canonical YYYY-MM-DD text")
    frame["date"] = parsed_dates

    if sorted(frame["seed"].unique().tolist()) != source["seeds"]:
        raise PrequentialInputError("Prediction seeds must be exactly 0 through 4")
    if sorted(frame["fold"].unique().tolist()) != source["folds"]:
        raise PrequentialInputError("Prediction folds must be exactly 1 through 3")
    if sorted(frame["station"].unique().tolist()) != sorted(source["stations"]):
        raise PrequentialInputError("Prediction station set changed")
    if not (frame["model_input_schema"] == source["model_input_schema"]).all():
        raise PrequentialInputError("Prediction model_input_schema changed")
    if not (frame["model_input_channels"] == source["model_input_channels"]).all():
        raise PrequentialInputError("Prediction model_input_channels changed")
    geometry = frame["station_geometry_sha256"].unique().tolist()
    if (
        len(geometry) != 1
        or len(str(geometry[0])) != 64
        or any(character not in "0123456789abcdef" for character in str(geometry[0]))
    ):
        raise PrequentialInputError("Station geometry provenance is invalid")

    key_columns = ["seed", "fold", "date", "station"]
    if frame.duplicated(key_columns).any():
        raise PrequentialInputError("Prediction seed/fold/date/station keys are not unique")
    expected_per_seed_fold = source["dates_per_fold"] * len(source["stations"])
    for seed in source["seeds"]:
        for fold in source["folds"]:
            subset = frame[(frame["seed"] == seed) & (frame["fold"] == fold)]
            if len(subset) != expected_per_seed_fold:
                raise PrequentialInputError(
                    f"Seed {seed} fold {fold} does not contain a complete station-date grid"
                )
            dates = pd.DatetimeIndex(sorted(subset["date"].unique()))
            if len(dates) != source["dates_per_fold"]:
                raise PrequentialInputError(
                    f"Seed {seed} fold {fold} must contain 287 unique dates"
                )
            expected_dates = pd.date_range(dates[0], dates[-1], freq="D")
            if not dates.equals(expected_dates):
                raise PrequentialInputError(
                    f"Seed {seed} fold {fold} dates are not daily and contiguous"
                )
            counts = subset.groupby("date", observed=True)["station"].nunique()
            if not (counts == len(source["stations"])).all():
                raise PrequentialInputError(
                    f"Seed {seed} fold {fold} has an incomplete station day"
                )

    date_fold_count = frame[["fold", "date"]].drop_duplicates().groupby("date")[
        "fold"
    ].nunique()
    if not (date_fold_count == 1).all():
        raise PrequentialInputError("A target date cannot belong to multiple folds")
    fold_ranges: list[pd.DatetimeIndex] = []
    for fold in source["folds"]:
        fold_dates = pd.DatetimeIndex(
            sorted(frame.loc[frame["fold"] == fold, "date"].unique())
        )
        fold_ranges.append(fold_dates)
    all_dates = fold_ranges[0].append(fold_ranges[1:])
    if not all_dates.equals(pd.date_range(all_dates[0], all_dates[-1], freq="D")):
        raise PrequentialInputError("Fold target-date blocks must form one daily sequence")

    same_key = source["same_seed_key_fields"]
    for column in source["same_seed_value_fields"]:
        unique_counts = frame.groupby(same_key, observed=True, sort=False)[column].nunique(
            dropna=False
        )
        if not (unique_counts == 1).all():
            raise PrequentialInputError(
                f"Cross-seed {column} values disagree for the same fold/date/station"
            )
    for lower, middle, upper in source["quantile_orders"]:
        if not (
            (frame[lower] <= frame[middle]) & (frame[middle] <= frame[upper])
        ).all():
            raise PrequentialInputError(
                f"Prediction quantiles cross in {lower}/{middle}/{upper}"
            )

    station_order = {station: index for index, station in enumerate(source["stations"])}
    frame["_station_order"] = frame["station"].map(station_order)
    frame = frame.sort_values(
        ["fold", "date", "_station_order", "seed"], kind="mergesort"
    ).drop(columns="_station_order")
    frame = frame.reset_index(drop=True)
    actual_rows, lag1_rows, first_prior_date = (
        _validate_actual_and_persistence_alignment(
            frame, persistence_reference, source
        )
    )
    return ValidatedSource(
        frame=frame,
        predictions_sha256=actual_sha,
        predictions_canonical_sha256=_canonical_frame_sha256(frame),
        predictions_size_bytes=size_bytes,
        source_manifest_sha256=_sha256_file(source_manifest_path),
        declared_output=record,
        persistence_reference_commit=persistence_reference.commit,
        persistence_reference_path=persistence_reference.path,
        persistence_reference_sha256=persistence_reference.sha256,
        persistence_reference_size_bytes=persistence_reference.size_bytes,
        persistence_reference_actual_rows=actual_rows,
        persistence_reference_lag1_rows=lag1_rows,
        persistence_reference_first_prior_date=first_prior_date,
    )


def _consolidate_predictions(
    frame: pd.DataFrame, profile: dict[str, Any]
) -> pd.DataFrame:
    key = ["fold", "date", "station"]
    base = frame.loc[frame["seed"] == 0, [*key, "actual", "persistence"]].copy()
    p50 = frame.pivot(index=key, columns="seed", values="p50").rename(
        columns={seed: f"seed{seed}_p50" for seed in profile["source_contract"]["seeds"]}
    )
    result = base.merge(p50.reset_index(), on=key, how="left", validate="one_to_one")
    station_order = {
        station: index
        for index, station in enumerate(profile["source_contract"]["stations"])
    }
    result["_station_order"] = result["station"].map(station_order)
    result = result.sort_values(
        ["fold", "date", "_station_order"], kind="mergesort"
    ).drop(columns="_station_order")
    return result.reset_index(drop=True)


def _finite_sample_higher(values: list[float], alpha: float) -> float:
    if not values:
        return math.nan
    ordered = np.sort(np.asarray(values, dtype=float))
    rank = math.ceil((len(ordered) + 1) * (1.0 - alpha))
    rank = min(max(rank, 1), len(ordered))
    return float(ordered[rank - 1])


def _expert_weights(state: _StationState) -> tuple[np.ndarray, float]:
    issue_index = state.history_count + 1
    eta = math.sqrt(8.0 * math.log(state.expert_count) / issue_index)
    log_weight = -eta * state.cumulative_loss
    log_weight -= float(np.max(log_weight))
    weight = np.exp(log_weight)
    weight /= float(np.sum(weight))
    return weight, eta


def _canonical_scalar(value: Any) -> Any:
    if value is None or (
        isinstance(value, (float, np.floating)) and math.isnan(float(value))
    ):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    return value


def _station_state_sha256(state: _StationState) -> str:
    payload = {
        "expert_count": state.expert_count,
        "initial_alpha": state.initial_alpha,
        "cumulative_loss": [float(value) for value in state.cumulative_loss],
        "history_count": state.history_count,
        "absolute_residuals": [float(value) for value in state.absolute_residuals],
        "underprediction_residuals": [
            float(value) for value in state.underprediction_residuals
        ],
        "drift_values": [float(value) for value in state.drift_values],
        "alpha": state.alpha,
        "next_issue_reset_reason": state.next_issue_reset_reason,
    }
    return _canonical_json_sha256(payload)


def _issue_batch_hash(previous_hash: str, rows: list[dict[str, Any]]) -> str:
    canonical_rows = [
        {column: _canonical_scalar(row[column]) for column in ISSUE_COLUMNS}
        for row in rows
    ]
    canonical = json.dumps(
        canonical_rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(f"{previous_hash}\n{canonical}".encode("utf-8")).hexdigest()


def _tail_p_value(current: float, past: list[float]) -> float:
    greater_equal = sum(value >= current for value in past)
    return (1.0 + greater_equal) / (1.0 + len(past))


def _drift_cut(
    values: list[float], *, delta: float, minimum_subwindow: int
) -> int | None:
    """Return a bounded Hoeffding two-window cut, or ``None``.

    This is deliberately named ADWIN-inspired: it scans admissible cuts of a
    bounded adaptive window, but does not claim byte-for-byte ADWIN parity.
    """

    n = len(values)
    if n < 2 * minimum_subwindow:
        return None
    array = np.asarray(values, dtype=float)
    log_term = math.log(4.0 * n / delta)
    best: tuple[float, int] | None = None
    for cut in range(minimum_subwindow, n - minimum_subwindow + 1):
        left = array[:cut]
        right = array[cut:]
        difference = abs(float(np.mean(left)) - float(np.mean(right)))
        bound = math.sqrt(
            0.5 * log_term * (1.0 / len(left) + 1.0 / len(right))
        )
        excess = difference - bound
        if excess > 0.0 and (best is None or excess > best[0]):
            best = (excess, cut)
    return None if best is None else best[1]


def _flags(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_status": profile["artifact_status"],
        "formal_warning_output": False,
        "independent_label_used": False,
        "confirmatory_external_validation": False,
        "vajont_used": False,
    }


def build_timelines(
    source_frame: pd.DataFrame, profile: dict[str, Any]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build station and site timelines with strict date-level issue barriers."""

    data = _consolidate_predictions(source_frame, profile)
    algorithm = profile["algorithm"]
    stations = profile["source_contract"]["stations"]
    minimum_history = int(algorithm["minimum_history"])
    maximum_history = int(algorithm["maximum_history"])
    target_coverage = float(algorithm["target_coverage"])
    aci = algorithm["aci"]
    drift = algorithm["drift"]
    expert_count = len(algorithm["expert_columns"])
    initial_alpha = float(aci["initial_alpha"])
    state: dict[str, _StationState] = {}
    current_fold: int | None = None
    previous_batch_hash = ZERO_HASH
    station_records: list[dict[str, Any]] = []
    site_records: list[dict[str, Any]] = []

    date_groups = data.groupby(["fold", "date"], sort=True, observed=True)
    for date_sequence, ((fold_value, date_value), date_frame) in enumerate(
        date_groups, start=1
    ):
        fold = int(fold_value)
        if fold != current_fold:
            reset_reason = "initial_fold_start" if current_fold is None else "fold_change"
            state = {
                station: _StationState(
                    expert_count=expert_count,
                    initial_alpha=initial_alpha,
                    next_issue_reset_reason=reset_reason,
                )
                for station in stations
            }
            current_fold = fold

        date_lookup = date_frame.set_index("station", verify_integrity=True)
        if set(date_lookup.index) != set(stations):
            raise PrequentialOutputError("Consolidated date lacks one or more stations")

        # Phase 1: issue every station using state that predates this target date.
        issued: list[dict[str, Any]] = []
        issued_experts: dict[str, np.ndarray] = {}
        for station in stations:
            station_state = state[station]
            source_row = date_lookup.loc[station]
            experts = np.asarray(
                [
                    source_row["persistence"],
                    source_row["seed0_p50"],
                    source_row["seed1_p50"],
                    source_row["seed2_p50"],
                    source_row["seed3_p50"],
                    source_row["seed4_p50"],
                ],
                dtype=float,
            )
            weights, eta = _expert_weights(station_state)
            point = float(np.dot(weights, experts))
            ready = station_state.history_count >= minimum_history
            qhat = (
                _finite_sample_higher(station_state.absolute_residuals, station_state.alpha)
                if ready
                else math.nan
            )
            lower = point - qhat if ready else math.nan
            upper = point + qhat if ready else math.nan
            row = {
                "fold": fold,
                "date": pd.Timestamp(date_value).strftime("%Y-%m-%d"),
                "station": station,
                "issue_date_sequence": date_sequence,
                "issue_state_reset_reason": station_state.next_issue_reset_reason,
                "issue_state_before_sha256": _station_state_sha256(station_state),
                "issue_history_count": station_state.history_count,
                "issue_forecast_action": "point_forecast" if ready else "abstain",
                "issue_uncertainty_action": (
                    "interval_available" if ready else "abstain_rewarm"
                ),
                "issue_eta": eta,
                "issue_expert_persistence_mm": float(experts[0]),
                "issue_expert_seed0_p50_mm": float(experts[1]),
                "issue_expert_seed1_p50_mm": float(experts[2]),
                "issue_expert_seed2_p50_mm": float(experts[3]),
                "issue_expert_seed3_p50_mm": float(experts[4]),
                "issue_expert_seed4_p50_mm": float(experts[5]),
                "issue_weight_persistence": float(weights[0]),
                "issue_weight_seed0": float(weights[1]),
                "issue_weight_seed1": float(weights[2]),
                "issue_weight_seed2": float(weights[3]),
                "issue_weight_seed3": float(weights[4]),
                "issue_weight_seed4": float(weights[5]),
                "issue_point_forecast_mm": point,
                "issue_fallback_persistence_mm": float(experts[0]),
                "issue_aci_alpha": station_state.alpha,
                "issue_conformal_history_count": len(
                    station_state.absolute_residuals
                ),
                "issue_conformal_q_mm": qhat,
                "issue_interval_lower_mm": lower,
                "issue_interval_upper_mm": upper,
            }
            issued.append(row)
            issued_experts[station] = experts
            station_state.next_issue_reset_reason = "none"

        batch_hash = _issue_batch_hash(previous_batch_hash, issued)
        for row in issued:
            row["previous_issue_batch_sha256"] = previous_batch_hash
            row["issue_batch_sha256"] = batch_hash

        # Phase 2: only now reveal all outcomes and update station state.
        revealed_for_site: dict[str, float] = {}
        for row in issued:
            station = str(row["station"])
            station_state = state[station]
            actual = float(date_lookup.loc[station, "actual"])
            experts = issued_experts[station]
            point = float(row["issue_point_forecast_mm"])
            absolute_error = abs(actual - point)
            persistence_error = abs(actual - float(experts[0]))
            underprediction = max(actual - point, 0.0)

            anomaly_ready = station_state.history_count >= minimum_history
            internal_anomaly_p = _tail_p_value(
                underprediction, station_state.underprediction_residuals
            )
            internal_anomaly_score = -math.log10(internal_anomaly_p)
            anomaly_p = internal_anomaly_p if anomaly_ready else math.nan
            anomaly_score = internal_anomaly_score if anomaly_ready else math.nan
            absolute_tail_p = _tail_p_value(
                absolute_error, station_state.absolute_residuals
            )
            surprise_rank = 1.0 - absolute_tail_p

            interval_available = math.isfinite(float(row["issue_interval_lower_mm"]))
            if interval_available:
                covered = bool(
                    float(row["issue_interval_lower_mm"])
                    <= actual
                    <= float(row["issue_interval_upper_mm"])
                )
                width = float(row["issue_interval_upper_mm"]) - float(
                    row["issue_interval_lower_mm"]
                )
                miss = 0.0 if covered else 1.0
                station_state.alpha = float(
                    np.clip(
                        station_state.alpha
                        + float(aci["gamma"])
                        * ((1.0 - target_coverage) - miss),
                        float(aci["minimum_alpha"]),
                        float(aci["maximum_alpha"]),
                    )
                )
                covered_output: bool | float = covered
            else:
                width = math.nan
                covered_output = math.nan

            expert_errors = np.abs(experts - actual)
            max_error = float(np.max(expert_errors))
            if max_error > 0.0:
                station_state.cumulative_loss += expert_errors / max_error
            station_state.history_count += 1
            station_state.absolute_residuals.append(absolute_error)
            station_state.underprediction_residuals.append(underprediction)
            station_state.drift_values.append(surprise_rank)
            station_state.absolute_residuals = station_state.absolute_residuals[
                -maximum_history:
            ]
            station_state.underprediction_residuals = (
                station_state.underprediction_residuals[-maximum_history:]
            )
            station_state.drift_values = station_state.drift_values[
                -int(drift["maximum_history"]):
            ]
            drift_cut = _drift_cut(
                station_state.drift_values,
                delta=float(drift["delta"]),
                minimum_subwindow=int(drift["minimum_subwindow"]),
            )
            drift_detected = drift_cut is not None
            alpha_after_update = station_state.alpha
            history_after_update = station_state.history_count
            updated_state_sha256 = _station_state_sha256(station_state)
            next_state = station_state
            if drift_detected:
                next_state = _StationState(
                    expert_count=expert_count,
                    initial_alpha=initial_alpha,
                    next_issue_reset_reason="drift_detected_previous_date",
                )
            state_after_sha256 = _station_state_sha256(next_state)

            row.update(
                {
                    "reveal_actual_mm": actual,
                    "reveal_point_absolute_error_mm": absolute_error,
                    "reveal_persistence_absolute_error_mm": persistence_error,
                    "reveal_interval_covered": covered_output,
                    "reveal_interval_width_mm": width,
                    "reveal_underprediction_residual_mm": underprediction,
                    "reveal_anomaly_p_value": anomaly_p,
                    "reveal_anomaly_score": anomaly_score,
                    "reveal_absolute_residual_surprise_rank": surprise_rank,
                    "reveal_drift_detected": drift_detected,
                    "reveal_drift_cut_index": (
                        float(drift_cut) if drift_cut is not None else math.nan
                    ),
                    "reveal_aci_alpha_after_update": alpha_after_update,
                    "reveal_history_count_after_update": history_after_update,
                    "reveal_state_reset_after_update": drift_detected,
                    "reveal_updated_state_sha256": updated_state_sha256,
                    "reveal_state_after_sha256": state_after_sha256,
                    "reveal_actual_used_for_future_update": True,
                    "reveal_phase": "after_all_same_date_station_issues",
                    **_flags(profile),
                }
            )
            station_records.append(row)
            if anomaly_ready:
                revealed_for_site[station] = anomaly_score

            state[station] = next_state

        blocks = algorithm["spatial_blocks"]
        block_summaries: dict[str, dict[str, Any]] = {}
        for name, members in blocks.items():
            available = [
                (station, revealed_for_site[station])
                for station in members
                if station in revealed_for_site
            ]
            if available:
                contributor, score = max(available, key=lambda item: item[1])
            else:
                contributor, score = "", math.nan
            block_summaries[name] = {
                "available": len(available),
                "expected": len(members),
                "contributor": contributor,
                "score": score,
            }
        all_blocks_available = all(
            summary["available"] > 0 for summary in block_summaries.values()
        )
        available_station_count = len(revealed_for_site)
        if available_station_count == len(stations):
            site_score_status = "complete_station_coverage"
        elif all_blocks_available:
            site_score_status = "available_subset_all_blocks"
        else:
            site_score_status = "abstain_spatial_incomplete"
        cross_block_score = (
            min(summary["score"] for summary in block_summaries.values())
            if all_blocks_available
            else math.nan
        )
        site_records.append(
            {
                "fold": fold,
                "date": pd.Timestamp(date_value).strftime("%Y-%m-%d"),
                "issue_batch_sha256": batch_hash,
                "outcome_phase": "after_all_same_date_station_outcomes_revealed",
                "available_station_score_count": available_station_count,
                "abstained_station_score_count": len(stations)
                - available_station_count,
                "block_o1_available_station_count": block_summaries["O1"][
                    "available"
                ],
                "block_o1_expected_station_count": block_summaries["O1"][
                    "expected"
                ],
                "block_o1_contributor_station": block_summaries["O1"][
                    "contributor"
                ],
                "block_o1_max_anomaly_score": block_summaries["O1"]["score"],
                "block_o2_available_station_count": block_summaries["O2"][
                    "available"
                ],
                "block_o2_expected_station_count": block_summaries["O2"][
                    "expected"
                ],
                "block_o2_contributor_station": block_summaries["O2"][
                    "contributor"
                ],
                "block_o2_max_anomaly_score": block_summaries["O2"]["score"],
                "block_o3_available_station_count": block_summaries["O3"][
                    "available"
                ],
                "block_o3_expected_station_count": block_summaries["O3"][
                    "expected"
                ],
                "block_o3_contributor_station": block_summaries["O3"][
                    "contributor"
                ],
                "block_o3_max_anomaly_score": block_summaries["O3"]["score"],
                "cross_block_min_of_block_max_anomaly_score": cross_block_score,
                "site_score_status": site_score_status,
                **_flags(profile),
            }
        )
        previous_batch_hash = batch_hash

    station_frame = pd.DataFrame.from_records(
        station_records, columns=STATION_TIMELINE_COLUMNS
    )
    site_frame = pd.DataFrame.from_records(site_records, columns=SITE_TIMELINE_COLUMNS)
    return station_frame, site_frame


def build_metrics(
    station_timeline: pd.DataFrame, profile: dict[str, Any]
) -> pd.DataFrame:
    """Summarize forecast, interval, abstention, drift, and anomaly behavior."""

    records: list[dict[str, Any]] = []
    stations = profile["source_contract"]["stations"]
    for fold in profile["source_contract"]["folds"]:
        fold_frame = station_timeline.loc[station_timeline["fold"] == fold]
        groups = [("station", station, fold_frame[fold_frame["station"] == station]) for station in stations]
        groups.append(("overall", "__overall__", fold_frame))
        for scope, station, group in groups:
            point_error = group["reveal_actual_mm"] - group["issue_point_forecast_mm"]
            persistence_error = (
                group["reveal_actual_mm"] - group["issue_fallback_persistence_mm"]
            )
            interval = group.loc[group["issue_interval_lower_mm"].notna()]
            active_point_error = (
                interval["reveal_actual_mm"] - interval["issue_point_forecast_mm"]
            )
            active_persistence_error = (
                interval["reveal_actual_mm"]
                - interval["issue_fallback_persistence_mm"]
            )
            point_rmse = math.sqrt(float(np.mean(point_error**2)))
            point_mae = float(np.mean(np.abs(point_error)))
            persistence_rmse = math.sqrt(float(np.mean(persistence_error**2)))
            persistence_mae = float(np.mean(np.abs(persistence_error)))
            if len(interval):
                active_point_rmse = math.sqrt(
                    float(np.mean(active_point_error**2))
                )
                active_point_mae = float(np.mean(np.abs(active_point_error)))
                active_persistence_rmse = math.sqrt(
                    float(np.mean(active_persistence_error**2))
                )
                active_persistence_mae = float(
                    np.mean(np.abs(active_persistence_error))
                )
                empirical_coverage = float(
                    interval["reveal_interval_covered"].astype(bool).mean()
                )
                interval_width = interval["reveal_interval_width_mm"].to_numpy(
                    dtype=float
                )
                actual = interval["reveal_actual_mm"].to_numpy(dtype=float)
                lower = interval["issue_interval_lower_mm"].to_numpy(dtype=float)
                upper = interval["issue_interval_upper_mm"].to_numpy(dtype=float)
                miscoverage = 1.0 - float(
                    profile["algorithm"]["target_coverage"]
                )
                interval_score = (
                    interval_width
                    + (2.0 / miscoverage) * np.maximum(lower - actual, 0.0)
                    + (2.0 / miscoverage) * np.maximum(actual - upper, 0.0)
                )
                mean_interval_width = float(np.mean(interval_width))
                mean_interval_score = float(np.mean(interval_score))
            else:
                active_point_rmse = math.nan
                active_point_mae = math.nan
                active_persistence_rmse = math.nan
                active_persistence_mae = math.nan
                empirical_coverage = math.nan
                mean_interval_width = math.nan
                mean_interval_score = math.nan
            anomaly = group["reveal_anomaly_score"].dropna().to_numpy(dtype=float)
            records.append(
                {
                    "fold": fold,
                    "scope": scope,
                    "station": station,
                    "rows": len(group),
                    "point_metric_population": "all_shadow_and_active_rows",
                    "point_rmse_mm": point_rmse,
                    "point_mae_mm": point_mae,
                    "persistence_rmse_mm": persistence_rmse,
                    "persistence_mae_mm": persistence_mae,
                    "point_rmse_skill_vs_persistence": (
                        1.0 - point_rmse / persistence_rmse
                        if persistence_rmse > 0.0
                        else math.nan
                    ),
                    "point_mae_skill_vs_persistence": (
                        1.0 - point_mae / persistence_mae
                        if persistence_mae > 0.0
                        else math.nan
                    ),
                    "active_point_rows": len(interval),
                    "active_point_rmse_mm": active_point_rmse,
                    "active_point_mae_mm": active_point_mae,
                    "active_persistence_rmse_mm": active_persistence_rmse,
                    "active_persistence_mae_mm": active_persistence_mae,
                    "active_point_rmse_skill_vs_persistence": (
                        1.0 - active_point_rmse / active_persistence_rmse
                        if active_persistence_rmse > 0.0
                        else math.nan
                    ),
                    "active_point_mae_skill_vs_persistence": (
                        1.0 - active_point_mae / active_persistence_mae
                        if active_persistence_mae > 0.0
                        else math.nan
                    ),
                    "interval_rows": len(interval),
                    "empirical_coverage": empirical_coverage,
                    "coverage_gap": empirical_coverage
                    - float(profile["algorithm"]["target_coverage"]),
                    "mean_interval_width_mm": mean_interval_width,
                    "mean_interval_score_80_mm": mean_interval_score,
                    "abstention_rows": int(
                        (group["issue_forecast_action"] == "abstain").sum()
                    ),
                    "abstention_rate": float(
                        (group["issue_forecast_action"] == "abstain").mean()
                    ),
                    "drift_count": int(group["reveal_drift_detected"].sum()),
                    "anomaly_rows": len(anomaly),
                    "mean_anomaly_score": (
                        float(np.mean(anomaly)) if len(anomaly) else math.nan
                    ),
                    "p95_anomaly_score": (
                        float(np.quantile(anomaly, 0.95, method="higher"))
                        if len(anomaly)
                        else math.nan
                    ),
                    "maximum_anomaly_score": (
                        float(np.max(anomaly)) if len(anomaly) else math.nan
                    ),
                    **_flags(profile),
                }
            )
    return pd.DataFrame.from_records(records, columns=METRIC_COLUMNS)


def _validate_output_fields(frame: pd.DataFrame, name: str) -> None:
    lowered = {column.lower() for column in frame.columns}
    overlap = lowered.intersection(FORBIDDEN_OUTPUT_FIELDS)
    if overlap:
        raise PrequentialOutputError(
            f"{name} contains prohibited output semantics: {sorted(overlap)}"
        )


def _validate_issue_hash_chain(
    station_timeline: pd.DataFrame,
    site_timeline: pd.DataFrame,
    profile: dict[str, Any],
) -> None:
    """Recompute the complete run-wide issue chain in canonical output order."""

    folds = profile["source_contract"]["folds"]
    stations = profile["source_contract"]["stations"]
    dates_per_fold = int(profile["source_contract"]["dates_per_fold"])
    try:
        site_dates = pd.to_datetime(
            site_timeline["date"], format="%Y-%m-%d", errors="raise"
        )
        station_dates = pd.to_datetime(
            station_timeline["date"], format="%Y-%m-%d", errors="raise"
        )
    except (TypeError, ValueError) as exc:
        raise PrequentialOutputError(
            "Timeline dates must be canonical YYYY-MM-DD values"
        ) from exc
    if not (
        site_dates.dt.strftime("%Y-%m-%d").to_numpy()
        == site_timeline["date"].to_numpy()
    ).all() or not (
        station_dates.dt.strftime("%Y-%m-%d").to_numpy()
        == station_timeline["date"].to_numpy()
    ).all():
        raise PrequentialOutputError(
            "Timeline dates must be canonical YYYY-MM-DD values"
        )

    expected_day_keys: list[tuple[int, str]] = []
    for fold in folds:
        fold_dates = sorted(
            site_timeline.loc[site_timeline["fold"] == fold, "date"].tolist()
        )
        if len(fold_dates) != dates_per_fold:
            raise PrequentialOutputError(
                f"Site timeline fold {fold} must have {dates_per_fold} dates"
            )
        expected_day_keys.extend((fold, date) for date in fold_dates)
    actual_day_keys = list(
        site_timeline.loc[:, ["fold", "date"]].itertuples(index=False, name=None)
    )
    if actual_day_keys != expected_day_keys:
        raise PrequentialOutputError("Site timeline fold/date order changed")

    expected_station_keys = [
        (fold, date, station)
        for fold, date in expected_day_keys
        for station in stations
    ]
    actual_station_keys = list(
        station_timeline.loc[:, ["fold", "date", "station"]].itertuples(
            index=False, name=None
        )
    )
    if actual_station_keys != expected_station_keys:
        raise PrequentialOutputError(
            "Station timeline fold/date/station order changed"
        )

    previous_hash = ZERO_HASH
    station_count = len(stations)
    for date_sequence, ((fold, date), site_row) in enumerate(
        zip(expected_day_keys, site_timeline.to_dict("records"), strict=True),
        start=1,
    ):
        start = (date_sequence - 1) * station_count
        batch = station_timeline.iloc[start : start + station_count]
        if not (batch["issue_date_sequence"] == date_sequence).all():
            raise PrequentialOutputError(
                f"Issue date sequence changed for fold {fold} date {date}"
            )
        if not (batch["previous_issue_batch_sha256"] == previous_hash).all():
            raise PrequentialOutputError(
                f"Issue hash previous link changed for fold {fold} date {date}"
            )
        expected_hash = _issue_batch_hash(
            previous_hash, batch.to_dict("records")
        )
        if not (batch["issue_batch_sha256"] == expected_hash).all():
            raise PrequentialOutputError(
                f"Issue batch hash does not match issue payload for fold {fold} date {date}"
            )
        if site_row["issue_batch_sha256"] != expected_hash:
            raise PrequentialOutputError(
                f"Site issue hash does not match station batch for fold {fold} date {date}"
            )
        previous_hash = expected_hash


def _validate_station_state_chain(station_timeline: pd.DataFrame) -> None:
    """Bind each station's effective reveal state to its next issue state."""

    ordered = station_timeline.sort_values(
        ["fold", "station", "date"], kind="mergesort"
    )
    previous_effective = ordered.groupby(["fold", "station"], sort=False)[
        "reveal_state_after_sha256"
    ].shift()
    continuing = previous_effective.notna()
    if not (
        ordered.loc[continuing, "issue_state_before_sha256"].to_numpy()
        == previous_effective.loc[continuing].to_numpy()
    ).all():
        raise PrequentialOutputError(
            "A station issue state does not match its previous effective reveal state"
        )


def _reconstruct_replay_source(station_timeline: pd.DataFrame) -> pd.DataFrame:
    """Recover only the immutable per-issue experts/outcomes needed for replay."""

    base_columns = [
        "fold",
        "date",
        "station",
        "reveal_actual_mm",
        "issue_expert_persistence_mm",
    ]
    pieces: list[pd.DataFrame] = []
    for seed in range(5):
        piece = station_timeline.loc[
            :, [*base_columns, f"issue_expert_seed{seed}_p50_mm"]
        ].copy()
        piece = piece.rename(
            columns={
                "reveal_actual_mm": "actual",
                "issue_expert_persistence_mm": "persistence",
                f"issue_expert_seed{seed}_p50_mm": "p50",
            }
        )
        piece["seed"] = seed
        pieces.append(piece)
    return pd.concat(pieces, ignore_index=True)


def _assert_replayed_frame_equal(
    actual: pd.DataFrame, expected: pd.DataFrame, name: str
) -> None:
    """Compare materialized numbers tightly while keeping text/hashes exact."""

    try:
        pd.testing.assert_frame_equal(
            actual.reset_index(drop=True),
            expected.reset_index(drop=True),
            check_dtype=False,
            check_exact=False,
            rtol=1e-14,
            atol=1e-14,
        )
    except AssertionError as exc:
        detail = str(exc).splitlines()[0][:300]
        raise PrequentialOutputError(
            f"{name} disagrees with independent causal replay: {detail}"
        ) from exc


def _validate_independent_replay(
    station_timeline: pd.DataFrame,
    site_timeline: pd.DataFrame,
    metrics: pd.DataFrame,
    profile: dict[str, Any],
) -> None:
    """Replay every online state transition from issue experts and revealed actuals."""

    source = _reconstruct_replay_source(station_timeline)
    numeric = source.select_dtypes(include=[np.number]).to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        raise PrequentialOutputError(
            "Issue experts and revealed actuals must be fully finite"
        )
    replayed_station, replayed_site = build_timelines(source, profile)
    _assert_replayed_frame_equal(
        station_timeline, replayed_station, "Station timeline"
    )
    _assert_replayed_frame_equal(site_timeline, replayed_site, "Site timeline")
    replayed_metrics = build_metrics(replayed_station, profile)
    _assert_replayed_frame_equal(metrics, replayed_metrics, "Metrics")


def validate_outputs(
    station_timeline: pd.DataFrame,
    site_timeline: pd.DataFrame,
    metrics: pd.DataFrame,
    profile: dict[str, Any],
) -> None:
    """Check the complete in-memory bundle before staging or promotion."""

    expected_station_rows = (
        len(profile["source_contract"]["folds"])
        * profile["source_contract"]["dates_per_fold"]
        * len(profile["source_contract"]["stations"])
    )
    expected_site_rows = (
        len(profile["source_contract"]["folds"])
        * profile["source_contract"]["dates_per_fold"]
    )
    if len(station_timeline) != expected_station_rows:
        raise PrequentialOutputError(
            f"Station timeline must have {expected_station_rows} rows"
        )
    if len(site_timeline) != expected_site_rows:
        raise PrequentialOutputError(f"Site timeline must have {expected_site_rows} rows")
    expected_metric_rows = len(profile["source_contract"]["folds"]) * (
        len(profile["source_contract"]["stations"]) + 1
    )
    if len(metrics) != expected_metric_rows:
        raise PrequentialOutputError(f"Metrics must have {expected_metric_rows} rows")
    if tuple(station_timeline.columns) != STATION_TIMELINE_COLUMNS:
        raise PrequentialOutputError("Station timeline schema changed")
    if tuple(site_timeline.columns) != SITE_TIMELINE_COLUMNS:
        raise PrequentialOutputError("Site timeline schema changed")
    if tuple(metrics.columns) != METRIC_COLUMNS:
        raise PrequentialOutputError("Metrics schema changed")
    for name, frame in (
        ("station_timeline", station_timeline),
        ("site_timeline", site_timeline),
        ("metrics", metrics),
    ):
        _validate_output_fields(frame, name)
        for flag, expected in _flags(profile).items():
            if not (frame[flag] == expected).all():
                raise PrequentialOutputError(f"{name} has invalid {flag}")
    if station_timeline.duplicated(["fold", "date", "station"]).any():
        raise PrequentialOutputError("Station timeline keys are not unique")
    if site_timeline.duplicated(["fold", "date"]).any():
        raise PrequentialOutputError("Site timeline keys are not unique")
    for column in (
        "issue_state_before_sha256",
        "reveal_updated_state_sha256",
        "reveal_state_after_sha256",
    ):
        state_hashes = station_timeline[column].astype(str)
        if not state_hashes.map(
            lambda value: len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        ).all():
            raise PrequentialOutputError(f"Station state hash is malformed: {column}")
    _validate_issue_hash_chain(station_timeline, site_timeline, profile)
    _validate_station_state_chain(station_timeline)
    _validate_independent_replay(
        station_timeline, site_timeline, metrics, profile
    )
    warm = station_timeline["issue_history_count"] < profile["algorithm"][
        "minimum_history"
    ]
    if not (station_timeline.loc[warm, "issue_forecast_action"] == "abstain").all():
        raise PrequentialOutputError("Warm histories must abstain")
    if station_timeline.loc[warm, "issue_interval_lower_mm"].notna().any():
        raise PrequentialOutputError("Warm histories cannot issue intervals")
    weighted = station_timeline.loc[
        :,
        [
            "issue_weight_persistence",
            "issue_weight_seed0",
            "issue_weight_seed1",
            "issue_weight_seed2",
            "issue_weight_seed3",
            "issue_weight_seed4",
        ],
    ].sum(axis=1)
    if not np.allclose(weighted.to_numpy(dtype=float), 1.0, atol=1e-12):
        raise PrequentialOutputError("Expert weights do not sum to one")


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False, lineterminator="\n", float_format="%.17g")


def _read_materialized_csv(path: Path, name: str) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path, float_precision="round_trip")
    except (OSError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise PrequentialOutputError(
            f"Cannot reload staged {name} CSV with round-trip precision"
        ) from exc
    if name == "site_timeline":
        for column in (
            "block_o1_contributor_station",
            "block_o2_contributor_station",
            "block_o3_contributor_station",
        ):
            if column in frame:
                frame[column] = frame[column].fillna("")
    return frame


def read_materialized_bundle(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    profile: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Reload and independently validate all three materialized CSV payloads."""

    if profile is None:
        profile = load_config()
    output_dir = Path(output_dir)
    names = profile["outputs"]
    station = _read_materialized_csv(
        output_dir / names["station_timeline"], "station_timeline"
    )
    site = _read_materialized_csv(
        output_dir / names["site_timeline"], "site_timeline"
    )
    metrics = _read_materialized_csv(output_dir / names["metrics"], "metrics")
    validate_outputs(station, site, metrics, profile)
    return station, site, metrics


def _output_record(staged: Path, target: Path, frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "path": _manifest_path(target),
        "sha256": _sha256_file(staged),
        "size_bytes": staged.stat().st_size,
        "rows": len(frame),
        "columns": list(frame.columns),
    }


def _build_manifest(
    *,
    profile: dict[str, Any],
    config_path: Path,
    predictions_path: Path,
    source_manifest_path: Path,
    source: ValidatedSource,
    outputs: dict[str, dict[str, Any]],
    terminal_issue_hash: str,
) -> dict[str, Any]:
    return {
        "schema_version": "ootang_prequential_monitor_manifest_v1",
        "artifact_kind": ARTIFACT_KIND,
        **_flags(profile),
        "case": "ootang",
        "default_pipeline_member": False,
        "profile": {
            "id": profile["profile_id"],
            "version": profile["profile_version"],
            "path": _manifest_path(config_path),
            "file_sha256": _sha256_file(config_path),
            "content_sha256": _canonical_json_sha256(profile),
        },
        "implementation": {
            "runner": {
                "path": _manifest_path(Path(__file__)),
                "sha256": _sha256_file(Path(__file__)),
            },
            "atomic_promotion_helper": {
                "path": _manifest_path(ROOT / "code" / "warning" / "draft_evidence.py"),
                "sha256": _sha256_file(
                    ROOT / "code" / "warning" / "draft_evidence.py"
                ),
            },
            "project": {
                "path": "pyproject.toml",
                "sha256": _sha256_file(ROOT / "pyproject.toml"),
            },
            "dependency_lock": {
                "path": "uv.lock",
                "sha256": _sha256_file(ROOT / "uv.lock"),
            },
        },
        "runtime": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
        "source": {
            "predictions": {
                "path": _manifest_path(predictions_path),
                "sha256": source.predictions_sha256,
                "canonical_order_independent_sha256": source.predictions_canonical_sha256,
                "size_bytes": source.predictions_size_bytes,
                "declared_rows": source.declared_output["rows"],
                "declared_columns": source.declared_output["columns"],
            },
            "manifest": {
                "path": _manifest_path(source_manifest_path),
                "sha256": source.source_manifest_sha256,
            },
            "persistence_reference": {
                "retrieval": "git_blob_at_source_manifest_commit",
                "commit": source.persistence_reference_commit,
                "path": source.persistence_reference_path,
                "sha256": source.persistence_reference_sha256,
                "size_bytes": source.persistence_reference_size_bytes,
                "manifest_record_sha256": source.persistence_reference_sha256,
                "manifest_record_size_bytes": (
                    source.persistence_reference_size_bytes
                ),
                "target_actual_rows_validated": (
                    source.persistence_reference_actual_rows
                ),
                "previous_natural_day_persistence_rows_validated": (
                    source.persistence_reference_lag1_rows
                ),
                "first_target_previous_reference_date": (
                    source.persistence_reference_first_prior_date
                ),
            },
            "validation": {
                "seeds": profile["source_contract"]["seeds"],
                "folds": profile["source_contract"]["folds"],
                "dates_per_fold": profile["source_contract"]["dates_per_fold"],
                "stations": profile["source_contract"]["stations"],
                "complete_unique_seed_fold_date_station_grid": True,
                "cross_seed_actual_persistence_provenance_equal": True,
                "finite_ordered_quantiles": True,
                "persistence_reference_git_blob_matches_manifest_hash_and_size": True,
                "target_actual_matches_reference_displacement_exactly": True,
                "persistence_matches_previous_natural_day_exactly": True,
            },
        },
        "algorithm": profile["algorithm"],
        "causality": {
            "same_date_execution": "all_station_issues_before_any_outcome_reveal",
            "actual_use": "outcome_reveal_then_update_for_future_target_dates_only",
            "model_version_change": "fold_change_resets_all_station_online_state",
            "drift_change": "detected_after_reveal_and_reset_applies_next_target_date",
            "station_state_binding": (
                "issue_state_before_and_reveal_effective_state_after_sha256"
            ),
        },
        "issue_hash_chain": {
            "kind": profile["replay_contract"]["issue_batch_hash"]["kind"],
            "initial_previous_hash": ZERO_HASH,
            "terminal_hash": terminal_issue_hash,
            "mode": "retrospective_replay_not_realtime_sealing",
            "same_date_actual_excluded": True,
            "canonicalization": profile["replay_contract"]["issue_batch_hash"],
        },
        "outcome_reveal": {
            "actual_used_after_reveal_for_update": True,
            "actual_used_in_same_date_issue": False,
        },
        "materialized_validation": {
            "csv_float_format": "%.17g",
            "csv_read_float_precision": "round_trip",
            "staged_csv_reloaded_before_promotion": True,
            "issue_chain_recomputed_from_reloaded_csv": True,
            "online_state_math_replayed_from_reloaded_csv": True,
            "site_aggregation_recomputed_from_reloaded_csv": True,
            "metrics_recomputed_from_reloaded_csv": True,
        },
        "outputs": outputs,
    }


def write_prequential_monitor(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    predictions_path: Path = DEFAULT_PREDICTIONS_PATH,
    source_manifest_path: Path = DEFAULT_SOURCE_MANIFEST_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> Path:
    """Validate, replay, stage, and promote one complete machine-monitor bundle."""

    config_path = Path(config_path).resolve()
    predictions_path = Path(predictions_path).resolve()
    source_manifest_path = Path(source_manifest_path).resolve()
    output_dir = Path(output_dir).resolve()
    profile = load_config(config_path)
    source = validate_source(predictions_path, source_manifest_path, profile)
    station_timeline, site_timeline = build_timelines(source.frame, profile)
    metrics = build_metrics(station_timeline, profile)
    validate_outputs(station_timeline, site_timeline, metrics, profile)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    names = profile["outputs"]
    targets = {
        "station_timeline": output_dir / names["station_timeline"],
        "site_timeline": output_dir / names["site_timeline"],
        "metrics": output_dir / names["metrics"],
        "manifest": output_dir / names["manifest"],
    }
    with tempfile.TemporaryDirectory(
        prefix=".ootang-prequential-monitor-", dir=output_dir.parent
    ) as directory:
        staging = Path(directory)
        staged = {
            "station_timeline": staging / names["station_timeline"],
            "site_timeline": staging / names["site_timeline"],
            "metrics": staging / names["metrics"],
        }
        frames = {
            "station_timeline": station_timeline,
            "site_timeline": site_timeline,
            "metrics": metrics,
        }
        for name, frame in frames.items():
            _write_csv(frame, staged[name])
        materialized_frames = {
            "station_timeline": _read_materialized_csv(
                staged["station_timeline"], "station_timeline"
            ),
            "site_timeline": _read_materialized_csv(
                staged["site_timeline"], "site_timeline"
            ),
            "metrics": _read_materialized_csv(staged["metrics"], "metrics"),
        }
        validate_outputs(
            materialized_frames["station_timeline"],
            materialized_frames["site_timeline"],
            materialized_frames["metrics"],
            profile,
        )
        output_records = {
            name: _output_record(
                staged[name], targets[name], materialized_frames[name]
            )
            for name in staged
        }
        terminal_hash = str(
            materialized_frames["site_timeline"].iloc[-1]["issue_batch_sha256"]
        )
        manifest = _build_manifest(
            profile=profile,
            config_path=config_path,
            predictions_path=predictions_path,
            source_manifest_path=source_manifest_path,
            source=source,
            outputs=output_records,
            terminal_issue_hash=terminal_hash,
        )
        staged_manifest = staging / names["manifest"]
        staged_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        replacements = tuple(
            [
                FileReplacement(staged[name], targets[name])
                for name in ("station_timeline", "site_timeline", "metrics")
            ]
            + [FileReplacement(staged_manifest, targets["manifest"])]
        )
        promote_staged_files(replacements)
    return targets["manifest"]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Ootang retrospective causal prequential monitor."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS_PATH)
    parser.add_argument(
        "--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST_PATH
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        manifest = write_prequential_monitor(
            config_path=args.config,
            predictions_path=args.predictions,
            source_manifest_path=args.source_manifest,
            output_dir=args.output_dir,
        )
    except (PrequentialConfigError, PrequentialInputError, PrequentialOutputError) as exc:
        print(f"[prequential-monitor] blocked: {exc}", file=sys.stderr)
        return 2
    print(f"[prequential-monitor] retrospective bundle: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ARTIFACT_KIND",
    "ARTIFACT_STATUS",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_PREDICTIONS_PATH",
    "DEFAULT_SOURCE_MANIFEST_PATH",
    "ISSUE_COLUMNS",
    "PrequentialConfigError",
    "PrequentialInputError",
    "PrequentialOutputError",
    "ValidatedSource",
    "build_metrics",
    "build_timelines",
    "load_config",
    "read_materialized_bundle",
    "validate_outputs",
    "validate_source",
    "write_prequential_monitor",
]
