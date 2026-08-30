"""Automatic future-state labels for the Ootang NGBoost research pilot.

This module implements only the first, labels-only increment recorded in
``docs/ootang_ngboost_auto_state_experiment_plan.md``.  It does not train
NGBoost and its five colours are data-driven deformation-state proxies, not
field-validated hazard truth.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.cluster import KMeans  # noqa: E402
from threadpoolctl import threadpool_limits  # noqa: E402


CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.draft_evidence import OOTANG_STATIONS  # noqa: E402
from warning.interval_state import classify_observed_interval_states  # noqa: E402
from warning.levels import WARNING_COLORS  # noqa: E402
from warning.stable_segment import select_initial_stable_segment  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
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
DEFAULT_RUNS_PATH = DEFAULT_PREDICTIONS_PATH.with_name("seed_stability_runs.csv")
DEFAULT_PREDICTIONS_MANIFEST_PATH = DEFAULT_PREDICTIONS_PATH.with_name("manifest.json")
DEFAULT_KINEMATICS_PATH = ROOT / "data" / "ootang_kinematics_long.csv"
DEFAULT_TOPOLOGY_PATH = ROOT / "config" / "ootang_operational_run.v4.draft.json"
DEFAULT_OUTPUT_DIR = ROOT / "figures" / "ngboost_auto_state_v1"
DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_ngboost_auto_state.v1.json"

ARTIFACT_KIND = "ootang_automatic_future_deformation_state_labels"
ARTIFACT_STATUS = "exploratory_proxy_labels_not_formal_warning"
BOUNDARY_VERSION = "fold1_h7_change_point_ordered_kmeans_v1"
OUTCOME_COLUMNS = (
    "future_displacement_rate_mm_per_day",
    "future_velocity_q90_mm_per_day",
    "future_acceleration_q90_mm_per_day_squared",
)
STANDARDIZED_OUTCOME_COLUMNS = tuple(f"standardized_{name}" for name in OUTCOME_COLUMNS)
SITE_BLOCKS_FALLBACK = {
    "O1": ("MJ9", "MJ1", "MJ3"),
    "O2": ("ATU4", "ATU5", "ATU3"),
    "O3": ("ATU2", "ATU1"),
}
OUTPUT_NAMES = {
    "station_labels": "station_auto_labels.csv",
    "site_labels": "site_auto_labels.csv",
    "state_definition": "label_state_definition.csv",
    "gate": "label_gate.json",
    "timeline": "auto_state_timeline.png",
    "manifest": "manifest.json",
}


class AutoStateInputError(RuntimeError):
    """Raised when an input violates the fixed labels-only protocol."""


@dataclass(frozen=True)
class AutoStateArtifacts:
    """Materialized outputs from one labels-only diagnostic run."""

    station_labels_path: Path
    site_labels_path: Path
    state_definition_path: Path
    gate_path: Path
    timeline_path: Path
    manifest_path: Path
    label_gate_passed: bool


@dataclass(frozen=True)
class _Boundaries:
    centers: tuple[float, ...]
    thresholds: tuple[float, ...]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _write_json(payload: dict[str, Any], path: Path) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(
        path,
        index=False,
        lineterminator="\n",
        float_format="%.12g",
        date_format="%Y-%m-%d",
    )


def _read_csv(path: Path, *, name: str) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except (FileNotFoundError, OSError, pd.errors.ParserError) as exc:
        raise AutoStateInputError(f"Cannot read {name}: {path}") from exc


def _resolve_config_path(value: object, *, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise AutoStateInputError(f"config {name} must be a nonempty path string")
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise AutoStateInputError(f"Cannot read auto-state config: {path}") from exc
    if not isinstance(config, dict):
        raise AutoStateInputError("Auto-state config must be a JSON object")
    fixed = {
        "schema_version": 1,
        "profile_version": 1,
        "case": "ootang",
        "formal_warning_output": False,
        "default_pipeline_member": False,
    }
    for key, expected in fixed.items():
        if config.get(key) != expected:
            raise AutoStateInputError(f"config {key} must be {expected!r}")
    for key in ("profile_id", "status"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise AutoStateInputError(f"config {key} must be a nonempty string")

    inputs = config.get("inputs")
    expected_inputs = {
        "predictions",
        "runs",
        "predictions_manifest",
        "kinematics",
        "topology",
    }
    if not isinstance(inputs, dict) or set(inputs) != expected_inputs:
        raise AutoStateInputError(
            f"config inputs must contain exactly {sorted(expected_inputs)}"
        )
    for key in expected_inputs:
        _resolve_config_path(inputs[key], name=f"inputs.{key}")
    _resolve_config_path(config.get("output_dir"), name="output_dir")

    algorithm = config.get("algorithm")
    expected_algorithm = {
        "horizon_days": 7,
        "label_fit_fold": 1,
        "label_fit_policy": "fold_test_anchors_complete_future_window",
        "beta_multiplier": 3.0,
        "min_segment_length": 7,
        "n_clusters": 5,
        "random_state": 0,
        "seed_aggregation": "equal_weight_mean",
        "standardization": "per_station_median_iqr",
        "assignment_policy": "fixed_fold1_boundaries",
    }
    if not isinstance(algorithm, dict):
        raise AutoStateInputError("config algorithm must be an object")
    for key, expected in expected_algorithm.items():
        if algorithm.get(key) != expected:
            raise AutoStateInputError(f"config algorithm.{key} must be {expected!r}")

    gates = config.get("gates")
    expected_gates = {
        "required_nonempty_folds": [1, 2],
        "min_class_support_advisory": 20,
        "block_on_limited_support": False,
    }
    if not isinstance(gates, dict):
        raise AutoStateInputError("config gates must be an object")
    for key, expected in expected_gates.items():
        if gates.get(key) != expected:
            raise AutoStateInputError(f"config gates.{key} must be {expected!r}")

    outputs = config.get("outputs")
    if not isinstance(outputs, dict) or outputs != OUTPUT_NAMES:
        raise AutoStateInputError(
            "config outputs must retain the six fixed artifact names"
        )
    if not isinstance(config.get("not_claimed"), list) or not config["not_claimed"]:
        raise AutoStateInputError("config not_claimed must be a nonempty list")
    return config


def _validate_prediction_manifest(
    path: Path,
    *,
    predictions_path: Path,
    runs_path: Path,
    predictions_rows: int,
    runs_rows: int,
) -> None:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise AutoStateInputError(f"Cannot read prediction manifest: {path}") from exc
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list):
        raise AutoStateInputError("Prediction manifest outputs must be a list")
    by_path = {
        str(record.get("path")): record
        for record in outputs
        if isinstance(record, dict) and isinstance(record.get("path"), str)
    }
    for source_path, rows, name in (
        (predictions_path, predictions_rows, "predictions"),
        (runs_path, runs_rows, "runs"),
    ):
        relative = _relative_path(source_path)
        record = by_path.get(relative)
        if record is None:
            raise AutoStateInputError(
                f"Prediction manifest lacks {name} output {relative}"
            )
        if record.get("sha256") != _sha256_file(source_path):
            raise AutoStateInputError(f"Prediction manifest hash mismatch for {name}")
        if record.get("rows") != rows:
            raise AutoStateInputError(
                f"Prediction manifest row-count mismatch for {name}"
            )


def _require_columns(frame: pd.DataFrame, columns: Iterable[str], *, name: str) -> None:
    missing = set(columns).difference(frame.columns)
    if missing:
        raise AutoStateInputError(f"{name} lacks columns: {sorted(missing)}")


def _parse_dates(
    frame: pd.DataFrame, columns: Iterable[str], *, name: str
) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        result[column] = pd.to_datetime(result[column], errors="coerce")
        if result[column].isna().any():
            raise AutoStateInputError(f"{name}.{column} contains invalid dates")
    return result


def _load_topology(path: Path) -> dict[str, tuple[str, ...]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise AutoStateInputError(f"Cannot read topology profile: {path}") from exc
    raw = payload.get("site_fusion", {}).get("spatial_blocks")
    if not isinstance(raw, dict):
        raise AutoStateInputError("Topology profile lacks site_fusion.spatial_blocks")
    blocks = {
        str(key): tuple(str(value) for value in values) for key, values in raw.items()
    }
    if blocks != SITE_BLOCKS_FALLBACK:
        raise AutoStateInputError(
            "Topology must retain the fixed O1/O2/O3 station groups"
        )
    members = [station for values in blocks.values() for station in values]
    if sorted(members) != sorted(OOTANG_STATIONS) or len(set(members)) != len(members):
        raise AutoStateInputError("Each Ootang station must occur in exactly one block")
    return blocks


def _load_fold_boundaries(runs: pd.DataFrame) -> pd.DataFrame:
    columns = (
        "fold",
        "seed",
        "fit_start_date",
        "fit_end_date",
        "calibration_end_date",
        "test_start_date",
        "test_end_date",
    )
    _require_columns(runs, columns, name="seed runs")
    frame = _parse_dates(
        runs.loc[:, columns],
        (
            "fit_start_date",
            "fit_end_date",
            "calibration_end_date",
            "test_start_date",
            "test_end_date",
        ),
        name="seed runs",
    )
    frame["fold"] = pd.to_numeric(frame["fold"], errors="coerce")
    frame["seed"] = pd.to_numeric(frame["seed"], errors="coerce")
    if frame[["fold", "seed"]].isna().any().any():
        raise AutoStateInputError("seed runs contains invalid fold or seed values")
    frame[["fold", "seed"]] = frame[["fold", "seed"]].astype(int)
    date_columns = [column for column in columns if column.endswith("_date")]
    for fold, group in frame.groupby("fold", sort=True):
        if group["seed"].duplicated().any():
            raise AutoStateInputError(f"Duplicate run row for fold={fold}")
        if any(group[column].nunique() != 1 for column in date_columns):
            raise AutoStateInputError(f"Seed boundary disagreement for fold={fold}")
    boundaries = frame.groupby("fold", sort=True)[date_columns].first().reset_index()
    expected_folds = tuple(range(1, len(boundaries) + 1))
    if tuple(boundaries["fold"]) != expected_folds:
        raise AutoStateInputError("OOF folds must be consecutive and start at one")
    if not (
        boundaries["fit_start_date"].lt(boundaries["fit_end_date"])
        & boundaries["fit_end_date"].lt(boundaries["calibration_end_date"])
        & boundaries["calibration_end_date"].lt(boundaries["test_start_date"])
        & boundaries["test_start_date"].le(boundaries["test_end_date"])
    ).all():
        raise AutoStateInputError("Invalid chronological fold boundaries")
    for previous, current in zip(
        boundaries.itertuples(index=False),
        boundaries.iloc[1:].itertuples(index=False),
        strict=False,
    ):
        if current.test_start_date != previous.test_end_date + pd.Timedelta(days=1):
            raise AutoStateInputError(
                "OOF test folds must be contiguous and non-overlapping"
            )
    return boundaries


def _aggregate_seed_predictions(
    predictions: pd.DataFrame,
    runs: pd.DataFrame,
    boundaries: pd.DataFrame,
) -> pd.DataFrame:
    columns = (
        "seed",
        "fold",
        "date",
        "station",
        "actual",
        "p50",
        "calibrated_p10",
        "calibrated_p90",
    )
    _require_columns(predictions, columns, name="seed predictions")
    frame = _parse_dates(
        predictions.loc[:, columns], ("date",), name="seed predictions"
    )
    frame["station"] = frame["station"].astype("string").str.strip()
    for column in ("seed", "fold"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[["seed", "fold"]].isna().any().any():
        raise AutoStateInputError("seed predictions contains invalid seed/fold values")
    frame[["seed", "fold"]] = frame[["seed", "fold"]].astype(int)
    if set(frame["station"]) != set(OOTANG_STATIONS):
        raise AutoStateInputError("seed predictions station set does not match Ootang")
    numeric_columns = ("actual", "p50", "calibrated_p10", "calibrated_p90")
    frame.loc[:, numeric_columns] = frame.loc[:, numeric_columns].apply(
        pd.to_numeric, errors="coerce"
    )
    if not np.isfinite(frame.loc[:, numeric_columns].to_numpy(dtype=float)).all():
        raise AutoStateInputError("seed predictions contains nonfinite required values")
    if frame.duplicated(["seed", "fold", "date", "station"]).any():
        raise AutoStateInputError("seed predictions contains duplicate keys")

    expected_seed_pairs = set(
        zip(runs["fold"].astype(int), runs["seed"].astype(int), strict=True)
    )
    observed_seed_pairs = set(zip(frame["fold"], frame["seed"], strict=True))
    if observed_seed_pairs != expected_seed_pairs:
        raise AutoStateInputError("prediction and run fold/seed sets disagree")
    expected_seed_count = runs["seed"].nunique()
    grouped = frame.groupby(["fold", "date", "station"], sort=True)
    if not grouped["seed"].nunique().eq(expected_seed_count).all():
        raise AutoStateInputError(
            "Every OOF key must contain the complete equal-weight seed set"
        )
    actual_spread = grouped["actual"].agg(lambda values: float(np.ptp(values)))
    if (actual_spread > 1e-9).any():
        raise AutoStateInputError("Actual displacement differs across seeds")

    aggregated = grouped[list(numeric_columns)].mean().reset_index()
    aggregated = aggregated.rename(
        columns={
            "calibrated_p10": "p10",
            "calibrated_p90": "p90",
        }
    )
    interval = classify_observed_interval_states(
        aggregated.loc[:, ["fold", "date", "station", "actual", "p10", "p50", "p90"]]
    )
    if not interval["interval_status"].eq("valid").all():
        raise AutoStateInputError("Aggregated calibrated intervals must all be valid")

    fold_dates = boundaries.set_index("fold")
    for fold, group in interval.groupby("fold", sort=True):
        boundary = fold_dates.loc[fold]
        if group["date"].min() != boundary["test_start_date"]:
            raise AutoStateInputError(f"fold={fold} starts outside its test boundary")
        if group["date"].max() != boundary["test_end_date"]:
            raise AutoStateInputError(f"fold={fold} ends outside its test boundary")
        counts = group.groupby("date")["station"].nunique()
        if not counts.eq(len(OOTANG_STATIONS)).all():
            raise AutoStateInputError(f"fold={fold} lacks all stations on every date")
    return interval.sort_values(["fold", "date", "station"], kind="stable").reset_index(
        drop=True
    )


def _load_kinematics(path: Path) -> pd.DataFrame:
    columns = (
        "case",
        "date",
        "station",
        "displacement",
        "velocity",
        "velocity_status",
        "acceleration",
        "acceleration_status",
    )
    frame = _read_csv(path, name="kinematics")
    _require_columns(frame, columns, name="kinematics")
    frame = _parse_dates(frame.loc[:, columns], ("date",), name="kinematics")
    frame["station"] = frame["station"].astype("string").str.strip()
    if not frame["case"].astype("string").eq("ootang").all():
        raise AutoStateInputError("Kinematics must contain only case=ootang")
    if set(frame["station"]) != set(OOTANG_STATIONS):
        raise AutoStateInputError("Kinematics station set does not match Ootang")
    if frame.duplicated(["date", "station"]).any():
        raise AutoStateInputError("Kinematics contains duplicate station/date keys")
    for column in ("displacement", "velocity", "acceleration"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for station, group in frame.groupby("station", sort=False):
        ordered = group.sort_values("date", kind="stable")
        if not ordered["date"].diff().dropna().dt.days.eq(1).all():
            raise AutoStateInputError(f"Kinematics is not daily for station={station}")
    return frame.sort_values(["station", "date"], kind="stable").reset_index(drop=True)


def _future_outcomes_for_period(
    kinematics: pd.DataFrame,
    *,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    horizon_days: int,
) -> pd.DataFrame:
    """Build target vectors whose entire future window stays in the period."""

    records: list[dict[str, Any]] = []
    last_anchor = end_date - pd.Timedelta(days=horizon_days)
    for station in OOTANG_STATIONS:
        group = (
            kinematics.loc[
                kinematics["station"].eq(station)
                & kinematics["date"].between(start_date, end_date)
            ]
            .sort_values("date", kind="stable")
            .reset_index(drop=True)
        )
        by_date = group.set_index("date")
        for anchor in pd.date_range(start_date, last_anchor, freq="D"):
            future_dates = pd.date_range(
                anchor + pd.Timedelta(days=1),
                anchor + pd.Timedelta(days=horizon_days),
                freq="D",
            )
            if (
                anchor not in by_date.index
                or not future_dates.isin(by_date.index).all()
            ):
                raise AutoStateInputError(
                    f"Incomplete future window for {station} at {anchor.date()}"
                )
            current = by_date.loc[anchor]
            future = by_date.loc[future_dates]
            valid = (
                future["velocity_status"].astype("string").eq("valid").all()
                and future["acceleration_status"].astype("string").eq("valid").all()
            )
            values = future.loc[:, ["velocity", "acceleration"]].to_numpy(dtype=float)
            if (
                not valid
                or not np.isfinite(values).all()
                or not np.isfinite(current["displacement"])
            ):
                raise AutoStateInputError(
                    f"Invalid strict future kinematics for {station} at {anchor.date()}"
                )
            end_displacement = float(by_date.loc[future_dates[-1], "displacement"])
            displacement_rate = (
                max(end_displacement - float(current["displacement"]), 0.0)
                / horizon_days
            )
            records.append(
                {
                    "date": anchor,
                    "station": station,
                    "target_end_date": future_dates[-1],
                    OUTCOME_COLUMNS[0]: displacement_rate,
                    OUTCOME_COLUMNS[1]: float(
                        np.quantile(np.maximum(future["velocity"], 0.0), 0.9)
                    ),
                    OUTCOME_COLUMNS[2]: float(
                        np.quantile(np.maximum(future["acceleration"], 0.0), 0.9)
                    ),
                }
            )
    return pd.DataFrame.from_records(records)


def _fit_station_scalers(
    fit_outcomes: pd.DataFrame,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    result = fit_outcomes.copy()
    definitions: list[dict[str, Any]] = []
    for station in OOTANG_STATIONS:
        mask = result["station"].eq(station)
        for source, target in zip(
            OUTCOME_COLUMNS, STANDARDIZED_OUTCOME_COLUMNS, strict=True
        ):
            values = result.loc[mask, source].to_numpy(dtype=float)
            median = float(np.median(values))
            q25, q75 = np.quantile(values, [0.25, 0.75])
            iqr = float(q75 - q25)
            if not np.isfinite(iqr) or iqr <= 0:
                raise AutoStateInputError(
                    f"Nonpositive fit IQR for station={station}, outcome={source}"
                )
            result.loc[mask, target] = (values - median) / iqr
            definitions.append(
                {
                    "record_type": "station_scaler",
                    "scope": station,
                    "component": source,
                    "median": median,
                    "iqr": iqr,
                    "max_input_date": result.loc[mask, "target_end_date"].max(),
                }
            )
    result["severity"] = result.loc[:, STANDARDIZED_OUTCOME_COLUMNS].mean(axis=1)
    return result, definitions


def _apply_station_scalers(
    outcomes: pd.DataFrame,
    definitions: list[dict[str, Any]],
) -> pd.DataFrame:
    result = outcomes.copy()
    lookup = {
        (record["scope"], record["component"]): (record["median"], record["iqr"])
        for record in definitions
        if record["record_type"] == "station_scaler"
    }
    for station in OOTANG_STATIONS:
        mask = result["station"].eq(station) & result[OUTCOME_COLUMNS[0]].notna()
        for source, target in zip(
            OUTCOME_COLUMNS, STANDARDIZED_OUTCOME_COLUMNS, strict=True
        ):
            median, iqr = lookup[(station, source)]
            result.loc[mask, target] = (result.loc[mask, source] - median) / iqr
    result["severity"] = result.loc[:, STANDARDIZED_OUTCOME_COLUMNS].mean(axis=1)
    return result


def _segment_constant_multivariate(
    values: np.ndarray,
    *,
    beta: float,
    min_segment_length: int,
) -> list[tuple[int, int]]:
    """Exact dynamic programming for SSE + beta * number_of_change_points."""

    values = np.asarray(values, dtype=float)
    if values.ndim != 2 or not np.isfinite(values).all():
        raise AutoStateInputError("Change-point input must be a finite 2-D matrix")
    n_rows = len(values)
    if n_rows < min_segment_length:
        raise AutoStateInputError(
            "Change-point input is shorter than one minimum segment"
        )
    prefix = np.vstack([np.zeros(values.shape[1]), np.cumsum(values, axis=0)])
    prefix_sq = np.concatenate([[0.0], np.cumsum(np.square(values).sum(axis=1))])
    best = np.full(n_rows + 1, np.inf)
    previous = np.full(n_rows + 1, -1, dtype=int)
    best[0] = 0.0
    for end in range(min_segment_length, n_rows + 1):
        starts = np.arange(0, end - min_segment_length + 1)
        eligible = np.isfinite(best[starts])
        starts = starts[eligible]
        if not len(starts):
            continue
        lengths = end - starts
        sums = prefix[end] - prefix[starts]
        costs = (
            prefix_sq[end] - prefix_sq[starts] - np.square(sums).sum(axis=1) / lengths
        )
        penalties = np.where(starts == 0, 0.0, beta)
        candidates = best[starts] + costs + penalties
        winner = int(np.argmin(candidates))
        best[end] = float(candidates[winner])
        previous[end] = int(starts[winner])
    if previous[n_rows] < 0:
        raise AutoStateInputError(
            "Change-point dynamic program found no valid partition"
        )
    segments: list[tuple[int, int]] = []
    end = n_rows
    while end > 0:
        start = int(previous[end])
        segments.append((start, end))
        end = start
    return list(reversed(segments))


def _station_segments(
    fit_scaled: pd.DataFrame,
    *,
    beta_multiplier: float,
    min_segment_length: int,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    segment_records: list[dict[str, Any]] = []
    cluster_rows: list[dict[str, Any]] = []
    for station in OOTANG_STATIONS:
        group = fit_scaled.loc[fit_scaled["station"].eq(station)].sort_values(
            "date", kind="stable"
        )
        matrix = group.loc[:, STANDARDIZED_OUTCOME_COLUMNS].to_numpy(dtype=float)
        beta = float(beta_multiplier * np.log(len(group)))
        segments = _segment_constant_multivariate(
            matrix, beta=beta, min_segment_length=min_segment_length
        )
        for segment_id, (start, end) in enumerate(segments, start=1):
            piece = group.iloc[start:end]
            severity = float(piece["severity"].mean())
            cluster_rows.append(
                {"scope": station, "severity": severity, "weight": len(piece)}
            )
            segment_records.append(
                {
                    "record_type": "station_segment",
                    "scope": station,
                    "segment_id": segment_id,
                    "segment_start_date": piece["date"].iloc[0],
                    "segment_end_date": piece["date"].iloc[-1],
                    "segment_length": len(piece),
                    "severity": severity,
                    "beta": beta,
                    "min_segment_length": min_segment_length,
                    "max_input_date": piece["target_end_date"].max(),
                }
            )
    return pd.DataFrame(cluster_rows), segment_records


def _ordered_kmeans_boundaries(
    segments: pd.DataFrame,
    *,
    n_clusters: int,
    random_state: int,
) -> _Boundaries:
    if len(segments) < n_clusters:
        raise AutoStateInputError("Fewer fitted segments than requested state classes")
    values = segments["severity"].to_numpy(dtype=float).reshape(-1, 1)
    weights = segments["weight"].to_numpy(dtype=float)
    if np.unique(values).size < n_clusters:
        raise AutoStateInputError(
            "Fewer distinct segment severities than state classes"
        )
    with threadpool_limits(limits=1):
        model = KMeans(
            n_clusters=n_clusters,
            init="k-means++",
            n_init=20,
            random_state=random_state,
            algorithm="lloyd",
        ).fit(values, sample_weight=weights)
    centers = tuple(
        sorted(float(value) for value in model.cluster_centers_.reshape(-1))
    )
    if not np.all(np.diff(centers) > 0):
        raise AutoStateInputError("Ordered state centers are not strictly increasing")
    thresholds = tuple(
        (left + right) / 2.0 for left, right in zip(centers, centers[1:])
    )
    return _Boundaries(centers=centers, thresholds=thresholds)


def _assign_levels(
    severity: pd.Series, boundaries: _Boundaries
) -> tuple[pd.Series, pd.Series]:
    numeric = pd.to_numeric(severity, errors="coerce").to_numpy(dtype=float)
    level_values = np.full(len(numeric), np.nan)
    finite = np.isfinite(numeric)
    level_values[finite] = np.digitize(
        numeric[finite], boundaries.thresholds, right=False
    )
    levels = pd.Series(level_values, index=severity.index, dtype="Int64")
    colors = levels.map(
        lambda value: pd.NA if pd.isna(value) else WARNING_COLORS[int(value)]
    ).astype("string")
    return levels, colors


def _class_definition_records(
    boundaries: _Boundaries,
    *,
    scope: str,
    record_type: str,
    training_labels: pd.Series,
) -> list[dict[str, Any]]:
    counts = training_labels.value_counts().reindex(
        range(len(boundaries.centers)), fill_value=0
    )
    records = []
    for level, center in enumerate(boundaries.centers):
        records.append(
            {
                "record_type": record_type,
                "scope": scope,
                "level": level,
                "color": WARNING_COLORS[level],
                "center": center,
                "lower_boundary": -np.inf
                if level == 0
                else boundaries.thresholds[level - 1],
                "upper_boundary": np.inf
                if level == len(boundaries.centers) - 1
                else boundaries.thresholds[level],
                "training_anchor_support": int(counts.loc[level]),
                "boundary_version": BOUNDARY_VERSION,
            }
        )
    return records


def _build_fold_v0(
    kinematics: pd.DataFrame,
    boundaries: pd.DataFrame,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    for fold_row in boundaries.itertuples(index=False):
        for station in OOTANG_STATIONS:
            result = select_initial_stable_segment(
                kinematics.loc[kinematics["station"].eq(station)],
                station=station,
                fit_end_date=fold_row.fit_end_date,
            )
            record = result.to_record()
            if result.status != "selected" or result.v0 is None:
                raise AutoStateInputError(
                    f"Fold-fit-only comparator failed for fold={fold_row.fold}, station={station}: "
                    f"{result.failure_reason}"
                )
            records.append(
                {
                    "fold": fold_row.fold,
                    "station": station,
                    "comparator_v0_mm_per_day": result.v0,
                    **{
                        f"v0_{key}": value
                        for key, value in record.items()
                        if key != "station"
                    },
                }
            )
    table = pd.DataFrame(records)
    definitions = [
        {
            "record_type": "fold_v0_comparator",
            "scope": row["station"],
            "fold": row["fold"],
            "fit_end_date": row["v0_fit_end_date"],
            "segment_start_date": row["v0_segment_start_date"],
            "segment_end_date": row["v0_segment_end_date"],
            "segment_length": row["v0_n_selected_velocities"],
            "mean_velocity": row["v0_mean_velocity"],
            "sigma": row["v0_sigma"],
            "comparator_v0_mm_per_day": row["comparator_v0_mm_per_day"],
            "comparator_role": "project_specific_fold_fit_only_not_formal_thesis_v0",
            "max_input_date": row["v0_fit_end_date"],
        }
        for row in records
    ]
    return table.loc[:, ["fold", "station", "comparator_v0_mm_per_day"]], definitions


def _build_four_indicator_oof(
    interval: pd.DataFrame,
    kinematics: pd.DataFrame,
    v0: pd.DataFrame,
) -> pd.DataFrame:
    kinetic = kinematics.loc[
        :,
        [
            "date",
            "station",
            "velocity",
            "velocity_status",
            "acceleration",
            "acceleration_status",
        ],
    ]
    result = interval.merge(
        kinetic, on=["date", "station"], how="left", validate="one_to_one"
    )
    result = result.merge(
        v0, on=["fold", "station"], how="left", validate="many_to_one"
    )
    valid = result["velocity_status"].eq("valid") & result["acceleration_status"].eq(
        "valid"
    )
    numeric = result.loc[
        :, ["interval_z", "velocity", "acceleration", "comparator_v0_mm_per_day"]
    ].to_numpy(dtype=float)
    if (
        not valid.all()
        or not np.isfinite(numeric).all()
        or (result["comparator_v0_mm_per_day"] <= 0).any()
    ):
        raise AutoStateInputError(
            "Every OOF feature row must have four finite strict indicators"
        )
    result = result.rename(
        columns={
            "velocity": "velocity_mm_per_day",
            "acceleration": "acceleration_mm_per_day_squared",
        }
    )
    result["tangent_angle_degree"] = np.degrees(
        np.arctan(result["velocity_mm_per_day"] / result["comparator_v0_mm_per_day"])
    )
    return result


def _attach_oof_outcomes(
    features: pd.DataFrame,
    kinematics: pd.DataFrame,
    boundaries: pd.DataFrame,
    *,
    horizon_days: int,
) -> pd.DataFrame:
    pieces = []
    for row in boundaries.itertuples(index=False):
        piece = _future_outcomes_for_period(
            kinematics,
            start_date=row.test_start_date,
            end_date=row.test_end_date,
            horizon_days=horizon_days,
        )
        piece["fold"] = row.fold
        pieces.append(piece)
    outcomes = pd.concat(pieces, ignore_index=True)
    result = features.merge(
        outcomes,
        on=["fold", "date", "station"],
        how="left",
        validate="one_to_one",
    )
    result["label_status"] = np.where(
        result["target_end_date"].notna(), "valid", "unavailable_fold_terminal"
    )
    return result


def _build_site_fit(
    station_fit: pd.DataFrame,
    blocks: dict[str, tuple[str, ...]],
) -> pd.DataFrame:
    pivot = station_fit.pivot(index="date", columns="station", values="severity")
    result = pd.DataFrame(index=pivot.index)
    for block, stations in blocks.items():
        result[f"block_{block}_severity"] = pivot.loc[:, list(stations)].mean(axis=1)
    for outcome in OUTCOME_COLUMNS[:2]:
        outcome_pivot = station_fit.pivot(
            index="date", columns="station", values=outcome
        )
        block_values = [
            outcome_pivot.loc[:, list(stations)].mean(axis=1)
            for stations in blocks.values()
        ]
        result[outcome] = pd.concat(block_values, axis=1).mean(axis=1)
    return result.reset_index()


def _fit_site_labeler(
    station_fit: pd.DataFrame,
    blocks: dict[str, tuple[str, ...]],
    *,
    beta_multiplier: float,
    min_segment_length: int,
    n_clusters: int,
    random_state: int,
) -> tuple[pd.DataFrame, _Boundaries, list[dict[str, Any]]]:
    site_fit = _build_site_fit(station_fit, blocks)
    definitions: list[dict[str, Any]] = []
    standardized = []
    for block in blocks:
        source = f"block_{block}_severity"
        target = f"standardized_{source}"
        values = site_fit[source].to_numpy(dtype=float)
        median = float(np.median(values))
        q25, q75 = np.quantile(values, [0.25, 0.75])
        iqr = float(q75 - q25)
        if not np.isfinite(iqr) or iqr <= 0:
            raise AutoStateInputError(f"Nonpositive site fit IQR for block={block}")
        site_fit[target] = (values - median) / iqr
        standardized.append(target)
        definitions.append(
            {
                "record_type": "site_scaler",
                "scope": "site",
                "component": source,
                "median": median,
                "iqr": iqr,
                "max_input_date": station_fit["target_end_date"].max(),
            }
        )
    site_fit["site_severity"] = site_fit.loc[:, standardized].mean(axis=1)
    matrix = site_fit.loc[:, standardized].to_numpy(dtype=float)
    beta = float(beta_multiplier * np.log(len(site_fit)))
    segments = _segment_constant_multivariate(
        matrix, beta=beta, min_segment_length=min_segment_length
    )
    cluster_rows = []
    for segment_id, (start, end) in enumerate(segments, start=1):
        piece = site_fit.iloc[start:end]
        severity = float(piece["site_severity"].mean())
        cluster_rows.append(
            {"scope": "site", "severity": severity, "weight": len(piece)}
        )
        definitions.append(
            {
                "record_type": "site_segment",
                "scope": "site",
                "segment_id": segment_id,
                "segment_start_date": piece["date"].iloc[0],
                "segment_end_date": piece["date"].iloc[-1],
                "segment_length": len(piece),
                "severity": severity,
                "beta": beta,
                "min_segment_length": min_segment_length,
                "max_input_date": station_fit["target_end_date"].max(),
            }
        )
    site_boundaries = _ordered_kmeans_boundaries(
        pd.DataFrame(cluster_rows), n_clusters=n_clusters, random_state=random_state
    )
    levels, colors = _assign_levels(site_fit["site_severity"], site_boundaries)
    site_fit["auto_state_level"] = levels
    site_fit["auto_state_color"] = colors
    definitions.extend(
        _class_definition_records(
            site_boundaries,
            scope="site",
            record_type="site_class",
            training_labels=levels,
        )
    )
    return site_fit, site_boundaries, definitions


def _apply_site_labeler(
    station_oof: pd.DataFrame,
    definitions: list[dict[str, Any]],
    boundaries: _Boundaries,
    blocks: dict[str, tuple[str, ...]],
) -> pd.DataFrame:
    valid = station_oof.loc[station_oof["label_status"].eq("valid")]
    pivot = valid.pivot(index=["fold", "date"], columns="station", values="severity")
    result = pd.DataFrame(index=pivot.index)
    scaler_lookup = {
        record["component"]: (record["median"], record["iqr"])
        for record in definitions
        if record["record_type"] == "site_scaler"
    }
    standardized = []
    for block, stations in blocks.items():
        source = f"block_{block}_severity"
        result[source] = pivot.loc[:, list(stations)].mean(axis=1)
        target = f"standardized_{source}"
        median, iqr = scaler_lookup[source]
        result[target] = (result[source] - median) / iqr
        standardized.append(target)
    for outcome in OUTCOME_COLUMNS[:2]:
        outcome_pivot = valid.pivot(
            index=["fold", "date"], columns="station", values=outcome
        )
        block_values = [
            outcome_pivot.loc[:, list(stations)].mean(axis=1)
            for stations in blocks.values()
        ]
        result[outcome] = pd.concat(block_values, axis=1).mean(axis=1)
    result["site_severity"] = result.loc[:, standardized].mean(axis=1)
    levels, colors = _assign_levels(result["site_severity"], boundaries)
    result["auto_state_level"] = levels
    result["auto_state_color"] = colors
    result = result.reset_index()
    target_dates = (
        valid.groupby(["fold", "date"])["target_end_date"].first().reset_index()
    )
    result = result.merge(target_dates, on=["fold", "date"], validate="one_to_one")

    all_dates = station_oof.loc[:, ["fold", "date"]].drop_duplicates()
    result = all_dates.merge(
        result, on=["fold", "date"], how="left", validate="one_to_one"
    )
    result["label_status"] = np.where(
        result["target_end_date"].notna(), "valid", "unavailable_fold_terminal"
    )
    return result.sort_values(["fold", "date"], kind="stable").reset_index(drop=True)


def _contiguous_run_summary(
    levels: pd.Series, dates: pd.Series, level: int
) -> dict[str, int]:
    mask = levels.eq(level).to_numpy()
    date_values = pd.to_datetime(dates).to_numpy()
    run_lengths: list[int] = []
    current = 0
    previous_date: np.datetime64 | None = None
    for active, date in zip(mask, date_values, strict=True):
        contiguous = previous_date is not None and (
            date - previous_date
        ) == np.timedelta64(1, "D")
        if active:
            current = current + 1 if current and contiguous else 1
        elif current:
            run_lengths.append(current)
            current = 0
        previous_date = date
    if current:
        run_lengths.append(current)
    return {"runs": len(run_lengths), "max_run_days": max(run_lengths, default=0)}


def _gate_record(name: str, passed: bool, evidence: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "evidence": evidence}


def _outcome_median_gate(frame: pd.DataFrame) -> tuple[bool, dict[str, Any]]:
    all_levels = list(range(5))
    medians = (
        frame.groupby("auto_state_level", observed=True)
        .agg(
            future_displacement_median=(OUTCOME_COLUMNS[0], "median"),
            future_velocity_median=(OUTCOME_COLUMNS[1], "median"),
        )
        .reindex(all_levels)
    )
    passed = bool(
        medians.notna().all().all()
        and np.all(np.diff(medians["future_displacement_median"]) >= 0)
        and np.all(np.diff(medians["future_velocity_median"]) >= 0)
    )
    evidence = {
        WARNING_COLORS[level]: {
            "future_displacement_median": float(
                medians.loc[level, "future_displacement_median"]
            ),
            "future_velocity_median": float(
                medians.loc[level, "future_velocity_median"]
            ),
        }
        for level in all_levels
    }
    return passed, evidence


def _build_gate(
    station_fit: pd.DataFrame,
    station_oof: pd.DataFrame,
    site_fit: pd.DataFrame,
    site_oof: pd.DataFrame,
    station_boundaries: _Boundaries,
    site_boundaries: _Boundaries,
    definitions: pd.DataFrame,
    boundaries: pd.DataFrame,
    *,
    horizon_days: int,
    label_fit_fold: int,
    required_nonempty_folds: tuple[int, ...],
    min_class_support_advisory: int,
) -> dict[str, Any]:
    gates: list[dict[str, Any]] = []
    advisories: list[dict[str, Any]] = []
    all_levels = list(range(5))
    station_fit_counts = (
        station_fit["auto_state_level"]
        .value_counts()
        .reindex(all_levels, fill_value=0)
        .astype(int)
    )
    site_fit_counts = (
        site_fit["auto_state_level"]
        .value_counts()
        .reindex(all_levels, fill_value=0)
        .astype(int)
    )
    gates.append(
        _gate_record(
            "label_fit_fold_five_levels_nonempty",
            bool(station_fit_counts.gt(0).all() and site_fit_counts.gt(0).all()),
            {
                "station_anchor_counts": {
                    WARNING_COLORS[i]: int(station_fit_counts.loc[i])
                    for i in all_levels
                },
                "site_day_counts": {
                    WARNING_COLORS[i]: int(site_fit_counts.loc[i]) for i in all_levels
                },
            },
        )
    )
    centers_pass = bool(
        np.all(np.diff(station_boundaries.centers) > 0)
        and np.all(np.diff(site_boundaries.centers) > 0)
    )
    gates.append(
        _gate_record(
            "five_centers_strictly_increasing",
            centers_pass,
            {
                "station_centers": station_boundaries.centers,
                "site_centers": site_boundaries.centers,
            },
        )
    )
    station_median_pass, station_median_evidence = _outcome_median_gate(station_fit)
    gates.append(
        _gate_record(
            "fit_station_outcome_medians_monotone_by_color",
            station_median_pass,
            station_median_evidence,
        )
    )
    site_median_pass, site_median_evidence = _outcome_median_gate(site_fit)
    gates.append(
        _gate_record(
            "fit_site_outcome_medians_monotone_by_color",
            site_median_pass,
            site_median_evidence,
        )
    )

    required_fold_evidence: dict[str, Any] = {}
    required_fold_pass = True
    for fold in required_nonempty_folds:
        station_counts = (
            station_oof.loc[
                station_oof["label_status"].eq("valid") & station_oof["fold"].eq(fold),
                "auto_state_level",
            ]
            .value_counts()
            .reindex(all_levels, fill_value=0)
            .astype(int)
        )
        site_counts = (
            site_oof.loc[
                site_oof["label_status"].eq("valid") & site_oof["fold"].eq(fold),
                "auto_state_level",
            ]
            .value_counts()
            .reindex(all_levels, fill_value=0)
            .astype(int)
        )
        fold_pass = bool(station_counts.gt(0).all() and site_counts.gt(0).all())
        required_fold_pass &= fold_pass
        required_fold_evidence[str(fold)] = {
            "passed": fold_pass,
            "station_anchor_counts": {
                WARNING_COLORS[level]: int(station_counts.loc[level])
                for level in all_levels
            },
            "site_day_counts": {
                WARNING_COLORS[level]: int(site_counts.loc[level])
                for level in all_levels
            },
        }
    gates.append(
        _gate_record(
            "required_folds_five_levels_nonempty",
            required_fold_pass,
            required_fold_evidence,
        )
    )

    support_evidence: dict[str, Any] = {}
    limited_support = False
    for fold in required_nonempty_folds:
        fold_station = station_oof.loc[
            station_oof["label_status"].eq("valid") & station_oof["fold"].eq(fold)
        ]
        fold_site = site_oof.loc[
            site_oof["label_status"].eq("valid") & site_oof["fold"].eq(fold)
        ]
        station_counts = (
            fold_station["auto_state_level"]
            .value_counts()
            .reindex(all_levels, fill_value=0)
        )
        site_counts = (
            fold_site["auto_state_level"]
            .value_counts()
            .reindex(all_levels, fill_value=0)
        )
        support_evidence[str(fold)] = {"station": {}, "site": {}}
        for level in all_levels:
            summary = _contiguous_run_summary(
                fold_site["auto_state_level"], fold_site["date"], level
            )
            station_count = int(station_counts.loc[level])
            site_count = int(site_counts.loc[level])
            limited_support |= (
                station_count < min_class_support_advisory
                or site_count < min_class_support_advisory
            )
            support_evidence[str(fold)]["station"][WARNING_COLORS[level]] = {
                "anchors": station_count,
                "limited_support": station_count < min_class_support_advisory,
            }
            support_evidence[str(fold)]["site"][WARNING_COLORS[level]] = {
                "days": site_count,
                **summary,
                "limited_support": site_count < min_class_support_advisory,
            }
    advisories.append(
        {
            "name": "limited_class_support",
            "triggered": limited_support,
            "blocking": False,
            "minimum_advisory_support": min_class_support_advisory,
            "evidence": support_evidence,
        }
    )

    evaluation_folds = [
        fold for fold in required_nonempty_folds if fold != label_fit_fold
    ]
    first_development_evaluation_date = boundaries.loc[
        boundaries["fold"].isin(evaluation_folds), "test_start_date"
    ].min()
    labeler_records = definitions["record_type"].ne("fold_v0_comparator")
    fit_input_dates = pd.to_datetime(
        definitions.loc[labeler_records, "max_input_date"], errors="coerce"
    ).dropna()
    max_fit_input_date = fit_input_dates.max()
    gates.append(
        _gate_record(
            "labeler_fit_inputs_precede_first_development_evaluation_fold",
            bool(max_fit_input_date < first_development_evaluation_date),
            {
                "max_labeler_fit_input_date": max_fit_input_date.strftime("%Y-%m-%d"),
                "first_development_evaluation_date": first_development_evaluation_date.strftime(
                    "%Y-%m-%d"
                ),
                "label_fit_fold": label_fit_fold,
            },
        )
    )
    valid_station = station_oof.loc[station_oof["label_status"].eq("valid")]
    gap = (valid_station["target_end_date"] - valid_station["date"]).dt.days
    fold_end = boundaries.set_index("fold")["test_end_date"]
    no_cross = valid_station.apply(
        lambda row: row["target_end_date"] <= fold_end.loc[row["fold"]], axis=1
    )
    terminal_counts = (
        station_oof.loc[station_oof["label_status"].eq("unavailable_fold_terminal")]
        .groupby(["fold", "station"])
        .size()
    )
    target_pass = bool(
        gap.eq(horizon_days).all()
        and no_cross.all()
        and terminal_counts.eq(horizon_days).all()
        and len(terminal_counts) == len(boundaries) * len(OOTANG_STATIONS)
    )
    gates.append(
        _gate_record(
            "future_window_exact_and_fold_isolated",
            target_pass,
            {
                "horizon_days": horizon_days,
                "valid_station_labels": len(valid_station),
                "terminal_rows_without_label": int(
                    station_oof["label_status"].ne("valid").sum()
                ),
                "cross_fold_targets": int((~no_cross).sum()),
            },
        )
    )
    advisories.append(
        {
            "name": "external_reproducibility_verification_required",
            "triggered": True,
            "blocking": False,
            "evidence": {
                "random_state": 0,
                "thread_limit": 1,
                "equal_seed_weights": True,
                "parameter_search": False,
                "reason": "a single process cannot attest its own second-run artifact hashes",
            },
        }
    )
    return {
        "artifact_kind": ARTIFACT_KIND,
        "artifact_status": ARTIFACT_STATUS,
        "formal_warning_output": False,
        "interpretation": "automatic_future_deformation_state_proxy_not_field_hazard_truth",
        "label_gate_passed": all(record["passed"] for record in gates),
        "fold_roles": {
            str(label_fit_fold): "automatic_taxonomy_and_future_classifier_training",
            "2": "development_evaluation_already_exposed",
            "3": "historical_evaluation_already_exposed",
        },
        "gates": gates,
        "advisories": advisories,
    }


def _plot_timeline(station: pd.DataFrame, site: pd.DataFrame, path: Path) -> None:
    palette = {
        "green": "#2ca02c",
        "blue": "#1f77b4",
        "yellow": "#f1c40f",
        "orange": "#ff7f0e",
        "red": "#d62728",
    }
    rows = [*OOTANG_STATIONS, "site"]
    fig, axes = plt.subplots(
        len(rows), 1, figsize=(14, 10), sharex=True, constrained_layout=True
    )
    for axis, name in zip(axes, rows, strict=True):
        frame = site if name == "site" else station.loc[station["station"].eq(name)]
        valid = frame.loc[frame["label_status"].eq("valid")]
        colors = valid["auto_state_color"].map(palette)
        axis.scatter(
            valid["date"],
            np.zeros(len(valid)),
            c=colors,
            marker="|",
            s=45,
            linewidths=1.2,
        )
        axis.set_ylabel(name, rotation=0, ha="right", va="center")
        axis.set_yticks([])
        axis.set_ylim(-1, 1)
        axis.grid(axis="x", alpha=0.2)
    axes[0].set_title("Ootang automatic future 7-day deformation-state proxies")
    axes[-1].set_xlabel(
        "Anchor date (final 7 days of each fold intentionally unlabeled)"
    )
    fig.savefig(path, dpi=150, metadata={"Software": "Landslide-Warning"})
    plt.close(fig)


def _normalise_definition_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(records)
    for column in (
        "segment_start_date",
        "segment_end_date",
        "fit_end_date",
        "max_input_date",
    ):
        if column in frame:
            parsed = pd.to_datetime(frame[column], errors="coerce")
            frame[column] = parsed.dt.strftime("%Y-%m-%d")
    preferred = [
        "record_type",
        "scope",
        "fold",
        "component",
        "segment_id",
        "segment_start_date",
        "segment_end_date",
        "segment_length",
        "level",
        "color",
        "median",
        "iqr",
        "severity",
        "center",
        "lower_boundary",
        "upper_boundary",
        "training_anchor_support",
        "comparator_v0_mm_per_day",
        "max_input_date",
        "boundary_version",
    ]
    columns = [column for column in preferred if column in frame.columns]
    columns.extend(column for column in frame.columns if column not in columns)
    return frame.loc[:, columns]


def run_auto_state_label_diagnostic(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> AutoStateArtifacts:
    """Build four-indicator OOF rows and fold-1-fixed automatic labels."""

    config_path = Path(config_path)
    config = _load_config(config_path)
    input_config = config["inputs"]
    predictions_path = _resolve_config_path(
        input_config["predictions"], name="inputs.predictions"
    )
    runs_path = _resolve_config_path(input_config["runs"], name="inputs.runs")
    predictions_manifest_path = _resolve_config_path(
        input_config["predictions_manifest"], name="inputs.predictions_manifest"
    )
    kinematics_path = _resolve_config_path(
        input_config["kinematics"], name="inputs.kinematics"
    )
    topology_path = _resolve_config_path(
        input_config["topology"], name="inputs.topology"
    )
    output_dir = _resolve_config_path(config["output_dir"], name="output_dir")
    algorithm = config["algorithm"]
    gate_config = config["gates"]
    horizon_days = int(algorithm["horizon_days"])
    label_fit_fold = int(algorithm["label_fit_fold"])
    beta_multiplier = float(algorithm["beta_multiplier"])
    min_segment_length = int(algorithm["min_segment_length"])
    n_clusters = int(algorithm["n_clusters"])
    random_state = int(algorithm["random_state"])

    predictions = _read_csv(predictions_path, name="seed predictions")
    runs = _read_csv(runs_path, name="seed runs")
    _validate_prediction_manifest(
        predictions_manifest_path,
        predictions_path=predictions_path,
        runs_path=runs_path,
        predictions_rows=len(predictions),
        runs_rows=len(runs),
    )
    fold_boundaries = _load_fold_boundaries(runs)
    if label_fit_fold not in set(fold_boundaries["fold"]):
        raise AutoStateInputError(
            "Configured label_fit_fold is absent from run boundaries"
        )
    blocks = _load_topology(topology_path)
    kinematics = _load_kinematics(kinematics_path)
    interval = _aggregate_seed_predictions(predictions, runs, fold_boundaries)
    v0, v0_definitions = _build_fold_v0(kinematics, fold_boundaries)
    features = _build_four_indicator_oof(interval, kinematics, v0)
    station_oof = _attach_oof_outcomes(
        features,
        kinematics,
        fold_boundaries,
        horizon_days=horizon_days,
    )

    label_fit_raw = station_oof.loc[
        station_oof["fold"].eq(label_fit_fold) & station_oof["label_status"].eq("valid")
    ].copy()
    if label_fit_raw.empty:
        raise AutoStateInputError(
            "Configured label-fit fold has no complete future windows"
        )
    station_fit, station_scaler_definitions = _fit_station_scalers(label_fit_raw)
    station_segments, station_segment_definitions = _station_segments(
        station_fit,
        beta_multiplier=beta_multiplier,
        min_segment_length=min_segment_length,
    )
    station_boundaries = _ordered_kmeans_boundaries(
        station_segments, n_clusters=n_clusters, random_state=random_state
    )
    station_fit["auto_state_level"], station_fit["auto_state_color"] = _assign_levels(
        station_fit["severity"], station_boundaries
    )
    station_class_definitions = _class_definition_records(
        station_boundaries,
        scope="all_stations",
        record_type="station_class",
        training_labels=station_fit["auto_state_level"],
    )

    site_fit, site_boundaries, site_definitions = _fit_site_labeler(
        station_fit,
        blocks,
        beta_multiplier=beta_multiplier,
        min_segment_length=min_segment_length,
        n_clusters=n_clusters,
        random_state=random_state,
    )
    station_oof = _apply_station_scalers(station_oof, station_scaler_definitions)
    station_oof["auto_state_level"], station_oof["auto_state_color"] = _assign_levels(
        station_oof["severity"], station_boundaries
    )
    station_oof.loc[
        station_oof["label_status"].ne("valid"),
        ["auto_state_level", "auto_state_color"],
    ] = pd.NA
    station_oof["auto_state_level"] = station_oof["auto_state_level"].astype("Int64")
    station_oof["boundary_version"] = BOUNDARY_VERSION
    site_oof = _apply_site_labeler(
        station_oof, site_definitions, site_boundaries, blocks
    )
    site_oof["auto_state_level"] = site_oof["auto_state_level"].astype("Int64")
    site_oof["boundary_version"] = BOUNDARY_VERSION

    definition_records = [
        *station_scaler_definitions,
        *station_segment_definitions,
        *station_class_definitions,
        *site_definitions,
        *v0_definitions,
    ]
    definition = _normalise_definition_frame(definition_records)
    gate = _build_gate(
        station_fit,
        station_oof,
        site_fit,
        site_oof,
        station_boundaries,
        site_boundaries,
        definition,
        fold_boundaries,
        horizon_days=horizon_days,
        label_fit_fold=label_fit_fold,
        required_nonempty_folds=tuple(
            int(value) for value in gate_config["required_nonempty_folds"]
        ),
        min_class_support_advisory=int(gate_config["min_class_support_advisory"]),
    )

    metadata = {
        "artifact_kind": ARTIFACT_KIND,
        "artifact_status": ARTIFACT_STATUS,
        "case": "ootang",
        "formal_warning_output": False,
    }
    for key, value in metadata.items():
        station_oof[key] = value
        site_oof[key] = value
    station_rank = {station: rank for rank, station in enumerate(OOTANG_STATIONS)}
    station_oof["_station_rank"] = station_oof["station"].map(station_rank)
    station_oof = (
        station_oof.sort_values(["fold", "date", "_station_rank"], kind="stable")
        .drop(columns="_station_rank")
        .reset_index(drop=True)
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    targets = {
        name: output_dir / filename for name, filename in config["outputs"].items()
    }
    _write_csv(station_oof, targets["station_labels"])
    _write_csv(site_oof, targets["site_labels"])
    _write_csv(definition, targets["state_definition"])
    _write_json(gate, targets["gate"])
    _plot_timeline(station_oof, site_oof, targets["timeline"])

    inputs = {
        "predictions": predictions_path,
        "runs": runs_path,
        "predictions_manifest": predictions_manifest_path,
        "kinematics": kinematics_path,
        "topology": topology_path,
        "config": config_path,
        "source_code": Path(__file__),
    }
    manifest = {
        **metadata,
        "boundary_version": BOUNDARY_VERSION,
        "interpretation": "automatic_future_deformation_state_proxy_not_field_hazard_truth",
        "label_gate_passed": gate["label_gate_passed"],
        "algorithm": {
            "horizon_days": horizon_days,
            "label_fit_fold": label_fit_fold,
            "label_fit_policy": algorithm["label_fit_policy"],
            "assignment_policy": algorithm["assignment_policy"],
            "beta": "3*log(n)",
            "beta_multiplier": beta_multiplier,
            "min_segment_length_days": min_segment_length,
            "n_clusters": n_clusters,
            "random_state": random_state,
            "seed_aggregation": "equal_weight_mean",
            "future_velocity_and_acceleration_quantile": 0.9,
            "positive_part": True,
            "parameter_search": False,
            "ngboost_trained": False,
        },
        "time_protocol": {
            "labeler_fit_start_date": station_fit["date"].min().strftime("%Y-%m-%d"),
            "labeler_last_anchor_date": station_fit["date"].max().strftime("%Y-%m-%d"),
            "labeler_max_input_date": station_fit["target_end_date"]
            .max()
            .strftime("%Y-%m-%d"),
            "first_development_evaluation_date": fold_boundaries.loc[
                fold_boundaries["fold"].eq(2), "test_start_date"
            ]
            .iloc[0]
            .strftime("%Y-%m-%d"),
            "fold_terminal_days_without_target": horizon_days,
            "fold_roles": gate["fold_roles"],
        },
        "row_counts": {
            "seed_predictions": len(predictions),
            "equal_weight_oof_station_rows": len(interval),
            "station_output_rows": len(station_oof),
            "station_valid_label_rows": int(
                station_oof["label_status"].eq("valid").sum()
            ),
            "site_output_rows": len(site_oof),
            "site_valid_label_rows": int(site_oof["label_status"].eq("valid").sum()),
            "state_definition_rows": len(definition),
        },
        "spatial_blocks": {key: list(values) for key, values in blocks.items()},
        "inputs": {
            name: {"path": _relative_path(path), "sha256": _sha256_file(path)}
            for name, path in inputs.items()
        },
        "outputs": {
            name: {"path": _relative_path(target), "sha256": _sha256_file(target)}
            for name, target in targets.items()
            if name != "manifest"
        },
        "not_claimed": config["not_claimed"],
    }
    _write_json(manifest, targets["manifest"])
    return AutoStateArtifacts(
        station_labels_path=targets["station_labels"],
        site_labels_path=targets["site_labels"],
        state_definition_path=targets["state_definition"],
        gate_path=targets["gate"],
        timeline_path=targets["timeline"],
        manifest_path=targets["manifest"],
        label_gate_passed=bool(gate["label_gate_passed"]),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    artifacts = run_auto_state_label_diagnostic(
        config_path=args.config,
    )
    print(
        json.dumps(
            {
                "label_gate_passed": artifacts.label_gate_passed,
                "manifest": str(artifacts.manifest_path),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AutoStateArtifacts",
    "AutoStateInputError",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_KINEMATICS_PATH",
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_PREDICTIONS_PATH",
    "DEFAULT_PREDICTIONS_MANIFEST_PATH",
    "DEFAULT_RUNS_PATH",
    "DEFAULT_TOPOLOGY_PATH",
    "run_auto_state_label_diagnostic",
]
