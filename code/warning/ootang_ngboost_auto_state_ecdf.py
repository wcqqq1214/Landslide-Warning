"""ECDF challenger for automatic Ootang future deformation-state labels.

The challenger is labels-only.  It reuses the version-one OOF, H=7,
four-indicator and lineage machinery, but replaces change-point clustering
with a pre-registered two-component empirical-CDF severity.  It never trains
NGBoost and does not produce a formal warning.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd


CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning import ootang_ngboost_auto_state as base  # noqa: E402
from warning.levels import WARNING_COLORS  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_ngboost_auto_state_ecdf.v2.json"
ARTIFACT_KIND = "ootang_automatic_future_deformation_state_ecdf_challenger"
ARTIFACT_STATUS = "exploratory_proxy_labels_not_formal_warning"
BOUNDARY_VERSION = "fold1_h7_station_ecdf_site_quantiles_v2"
TARGET_COMPONENTS = (
    "future_displacement_rate_mm_per_day",
    "future_velocity_q90_mm_per_day",
)
QUANTILE_PROBABILITIES = (0.2, 0.4, 0.6, 0.8)


def _load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise base.AutoStateInputError(f"Cannot read ECDF config: {path}") from exc
    fixed = {
        "schema_version": 1,
        "profile_version": 2,
        "case": "ootang",
        "formal_warning_output": False,
        "default_pipeline_member": False,
    }
    if not isinstance(config, dict):
        raise base.AutoStateInputError("ECDF config must be a JSON object")
    for key, expected in fixed.items():
        if config.get(key) != expected:
            raise base.AutoStateInputError(f"config {key} must be {expected!r}")
    if config.get("profile_id") != "ootang_ngboost_auto_state_ecdf_v2":
        raise base.AutoStateInputError("Unexpected ECDF profile_id")
    if config.get("status") != ARTIFACT_STATUS:
        raise base.AutoStateInputError("Unexpected ECDF status")

    expected_inputs = {"v1_station_labels", "v1_manifest", "topology"}
    inputs = config.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != expected_inputs:
        raise base.AutoStateInputError("ECDF config has an invalid input set")
    for name, value in inputs.items():
        base._resolve_config_path(value, name=f"inputs.{name}")
    base._resolve_config_path(config.get("output_dir"), name="output_dir")

    expected_algorithm = {
        "horizon_days": 7,
        "label_fit_fold": 1,
        "label_fit_policy": "fold1_complete_future_windows",
        "station_target_components": list(TARGET_COMPONENTS),
        "component_weights": [0.5, 0.5],
        "ecdf_side": "right",
        "station_boundaries": "fold1_pooled_station_severity_q20_q40_q60_q80_numpy_linear",
        "site_aggregation": "equal_station_within_block_then_equal_three_blocks",
        "site_boundaries": "fold1_site_severity_q20_q40_q60_q80_numpy_linear",
        "assignment_policy": "fixed_fold1_boundaries",
        "acceleration_target_role": "excluded_from_target_retained_in_features",
        "parameter_search": False,
    }
    if config.get("algorithm") != expected_algorithm:
        raise base.AutoStateInputError(
            "ECDF algorithm must retain the registered definition"
        )
    expected_gates = {
        "required_station_and_site_nonempty_fold": 1,
        "required_site_nonempty_fold": 2,
        "raw_outcome_monotone_folds": [1, 2],
        "min_class_support_advisory": 20,
        "block_on_limited_support": False,
    }
    if config.get("gates") != expected_gates:
        raise base.AutoStateInputError(
            "ECDF gates must retain the registered definition"
        )
    if config.get("outputs") != base.OUTPUT_NAMES:
        raise base.AutoStateInputError(
            "ECDF outputs must retain the six fixed filenames"
        )
    if not isinstance(config.get("not_claimed"), list) or not config["not_claimed"]:
        raise base.AutoStateInputError("ECDF not_claimed must be a nonempty list")
    return config


def _right_continuous_ecdf(
    values: np.ndarray | pd.Series,
    reference_sorted: np.ndarray | pd.Series,
) -> np.ndarray:
    """Evaluate F_n(x)=#{reference <= x}/n using ``side='right'``."""

    query = np.asarray(values, dtype=float)
    reference = np.asarray(reference_sorted, dtype=float)
    if reference.ndim != 1 or not len(reference) or not np.isfinite(reference).all():
        raise base.AutoStateInputError(
            "ECDF reference must be a nonempty finite vector"
        )
    if np.any(reference[:-1] > reference[1:]):
        raise base.AutoStateInputError("ECDF reference must be sorted")
    result = np.full(query.shape, np.nan, dtype=float)
    finite = np.isfinite(query)
    result[finite] = np.searchsorted(reference, query[finite], side="right") / len(
        reference
    )
    return result


def _quantile_boundaries(
    values: np.ndarray | pd.Series,
) -> tuple[float, float, float, float]:
    numeric = np.asarray(values, dtype=float)
    if numeric.ndim != 1 or not len(numeric) or not np.isfinite(numeric).all():
        raise base.AutoStateInputError(
            "Quantile input must be a nonempty finite vector"
        )
    result = np.quantile(numeric, QUANTILE_PROBABILITIES, method="linear")
    return tuple(float(value) for value in result)


def _assign_levels(
    values: np.ndarray | pd.Series,
    boundaries: tuple[float, float, float, float],
) -> pd.Series:
    numeric = np.asarray(values, dtype=float)
    assigned = np.full(numeric.shape, np.nan, dtype=float)
    finite = np.isfinite(numeric)
    assigned[finite] = np.searchsorted(boundaries, numeric[finite], side="right")
    return pd.Series(assigned, dtype="Int64")


def _fit_and_apply_station_ecdfs(
    station: pd.DataFrame,
    *,
    label_fit_fold: int,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    result = station.copy()
    definitions: list[dict[str, Any]] = []
    valid = result["label_status"].eq("valid")
    for station_name in base.OOTANG_STATIONS:
        fit_mask = (
            valid
            & result["fold"].eq(label_fit_fold)
            & result["station"].eq(station_name)
        )
        apply_mask = valid & result["station"].eq(station_name)
        for component in TARGET_COMPONENTS:
            reference = np.sort(result.loc[fit_mask, component].to_numpy(dtype=float))
            if not len(reference) or not np.isfinite(reference).all():
                raise base.AutoStateInputError(
                    f"Missing fold-1 ECDF reference for {station_name}/{component}"
                )
            output_column = f"ecdf_{component}"
            result.loc[apply_mask, output_column] = _right_continuous_ecdf(
                result.loc[apply_mask, component], reference
            )
            unique, counts = np.unique(reference, return_counts=True)
            cumulative = np.cumsum(counts)
            for value, count, cumulative_count in zip(
                unique, counts, cumulative, strict=True
            ):
                definitions.append(
                    {
                        "record_type": "station_ecdf",
                        "scope": station_name,
                        "component": component,
                        "unique_value": float(value),
                        "value_count": int(count),
                        "cumulative_count": int(cumulative_count),
                        "total_count": len(reference),
                        "cdf": float(cumulative_count / len(reference)),
                        "ecdf_side": "right",
                        "fit_fold": label_fit_fold,
                        "max_input_date": result.loc[fit_mask, "target_end_date"].max(),
                    }
                )
    ecdf_columns = tuple(f"ecdf_{component}" for component in TARGET_COMPONENTS)
    result["severity"] = result.loc[:, ecdf_columns].mean(axis=1, skipna=False)
    return result, definitions


def _boundary_records(
    boundaries: tuple[float, float, float, float],
    *,
    scope: str,
    fit_fold: int,
    max_input_date: pd.Timestamp,
) -> list[dict[str, Any]]:
    return [
        {
            "record_type": f"{scope}_boundary",
            "scope": scope,
            "boundary_index": index,
            "quantile_probability": probability,
            "boundary_value": value,
            "quantile_method": "numpy_linear",
            "fit_fold": fit_fold,
            "max_input_date": max_input_date,
            "boundary_version": BOUNDARY_VERSION,
        }
        for index, (probability, value) in enumerate(
            zip(QUANTILE_PROBABILITIES, boundaries, strict=True), start=1
        )
    ]


def _build_site_timeline(
    station: pd.DataFrame,
    blocks: dict[str, tuple[str, ...]],
    site_boundaries: tuple[float, float, float, float] | None = None,
) -> pd.DataFrame:
    valid = station.loc[station["label_status"].eq("valid")]
    index = ["fold", "date"]
    severity = valid.pivot(index=index, columns="station", values="severity")
    result = pd.DataFrame(index=severity.index)
    raw_pivots = {
        component: valid.pivot(index=index, columns="station", values=component)
        for component in TARGET_COMPONENTS
    }
    block_severity_columns = []
    for block, stations in blocks.items():
        severity_column = f"block_{block}_severity"
        result[severity_column] = severity.loc[:, list(stations)].mean(axis=1)
        block_severity_columns.append(severity_column)
        for component, pivot in raw_pivots.items():
            result[f"block_{block}_{component}"] = pivot.loc[:, list(stations)].mean(
                axis=1
            )
    result["site_severity"] = result.loc[:, block_severity_columns].mean(axis=1)
    for component in TARGET_COMPONENTS:
        result[f"site_{component}"] = result.loc[
            :, [f"block_{block}_{component}" for block in blocks]
        ].mean(axis=1)
    target_dates = valid.groupby(index, sort=True)["target_end_date"].first()
    result["target_end_date"] = target_dates
    result = result.reset_index()

    all_dates = station.loc[:, index].drop_duplicates()
    result = all_dates.merge(result, on=index, how="left", validate="one_to_one")
    result["label_status"] = np.where(
        result["target_end_date"].notna(), "valid", "unavailable_fold_terminal"
    )
    if site_boundaries is not None:
        result["auto_state_level"] = _assign_levels(
            result["site_severity"], site_boundaries
        ).array
        result["auto_state_color"] = (
            result["auto_state_level"]
            .map(lambda value: pd.NA if pd.isna(value) else WARNING_COLORS[int(value)])
            .astype("string")
        )
    return result.sort_values(index, kind="stable").reset_index(drop=True)


def _counts(frame: pd.DataFrame, *, fold: int) -> pd.Series:
    return (
        frame.loc[
            frame["label_status"].eq("valid") & frame["fold"].eq(fold),
            "auto_state_level",
        ]
        .value_counts()
        .reindex(range(5), fill_value=0)
        .astype(int)
    )


def _raw_site_medians(site: pd.DataFrame, *, fold: int) -> tuple[bool, dict[str, Any]]:
    frame = site.loc[site["label_status"].eq("valid") & site["fold"].eq(fold)]
    columns = tuple(f"site_{component}" for component in TARGET_COMPONENTS)
    medians = (
        frame.groupby("auto_state_level", observed=True)[list(columns)]
        .median()
        .reindex(range(5))
    )
    passed = bool(
        medians.notna().all().all()
        and all(np.all(np.diff(medians[column]) >= 0) for column in columns)
    )
    evidence = {
        WARNING_COLORS[level]: {
            TARGET_COMPONENTS[index]: (
                None
                if pd.isna(medians.loc[level, column])
                else float(medians.loc[level, column])
            )
            for index, column in enumerate(columns)
        }
        for level in range(5)
    }
    return passed, evidence


def _build_gate(
    station: pd.DataFrame,
    site: pd.DataFrame,
    definitions: pd.DataFrame,
    fold_boundaries: pd.DataFrame,
    station_boundaries: tuple[float, float, float, float],
    site_boundaries: tuple[float, float, float, float],
    *,
    horizon_days: int,
    min_class_support_advisory: int,
) -> dict[str, Any]:
    gates: list[dict[str, Any]] = []
    advisories: list[dict[str, Any]] = []
    boundary_pass = bool(
        np.all(np.diff(station_boundaries) > 0) and np.all(np.diff(site_boundaries) > 0)
    )
    gates.append(
        base._gate_record(
            "boundaries_strictly_increasing",
            boundary_pass,
            {"station": station_boundaries, "site": site_boundaries},
        )
    )

    fold1_station = _counts(station, fold=1)
    fold1_site = _counts(site, fold=1)
    gates.append(
        base._gate_record(
            "fold1_station_and_site_five_levels_nonempty",
            bool(fold1_station.gt(0).all() and fold1_site.gt(0).all()),
            {
                "station": {
                    WARNING_COLORS[i]: int(fold1_station.loc[i]) for i in range(5)
                },
                "site": {WARNING_COLORS[i]: int(fold1_site.loc[i]) for i in range(5)},
            },
        )
    )
    fold2_site = _counts(site, fold=2)
    gates.append(
        base._gate_record(
            "fold2_site_five_levels_nonempty",
            bool(fold2_site.gt(0).all()),
            {WARNING_COLORS[i]: int(fold2_site.loc[i]) for i in range(5)},
        )
    )
    median_evidence = {}
    median_pass = True
    for fold in (1, 2):
        passed, evidence = _raw_site_medians(site, fold=fold)
        median_pass &= passed
        median_evidence[str(fold)] = {"passed": passed, "medians": evidence}
    gates.append(
        base._gate_record(
            "fold1_fold2_site_raw_outcome_medians_monotone",
            median_pass,
            median_evidence,
        )
    )
    max_fit_input = pd.to_datetime(definitions["max_input_date"], errors="coerce").max()
    fold2_start = fold_boundaries.loc[
        fold_boundaries["fold"].eq(2), "test_start_date"
    ].iloc[0]
    gates.append(
        base._gate_record(
            "fit_inputs_end_before_fold2",
            bool(max_fit_input < fold2_start),
            {
                "max_fit_input_date": max_fit_input.strftime("%Y-%m-%d"),
                "fold2_start_date": fold2_start.strftime("%Y-%m-%d"),
            },
        )
    )
    valid_station = station.loc[station["label_status"].eq("valid")]
    gaps = (valid_station["target_end_date"] - valid_station["date"]).dt.days
    fold_end = fold_boundaries.set_index("fold")["test_end_date"]
    cross_fold = valid_station.apply(
        lambda row: row["target_end_date"] > fold_end.loc[row["fold"]], axis=1
    )
    terminal = (
        station.loc[station["label_status"].eq("unavailable_fold_terminal")]
        .groupby(["fold", "station"])
        .size()
    )
    target_pass = bool(
        gaps.eq(horizon_days).all()
        and not cross_fold.any()
        and terminal.eq(horizon_days).all()
        and len(terminal) == len(fold_boundaries) * len(base.OOTANG_STATIONS)
    )
    gates.append(
        base._gate_record(
            "future_window_exact_and_fold_isolated",
            target_pass,
            {
                "horizon_days": horizon_days,
                "cross_fold_targets": int(cross_fold.sum()),
                "valid_station_labels": len(valid_station),
                "terminal_rows_without_label": int(
                    station["label_status"].ne("valid").sum()
                ),
            },
        )
    )

    fold2_station_evidence = {}
    for station_name in base.OOTANG_STATIONS:
        frame = station.loc[
            station["label_status"].eq("valid")
            & station["fold"].eq(2)
            & station["station"].eq(station_name)
        ]
        counts = (
            frame["auto_state_level"].value_counts().reindex(range(5), fill_value=0)
        )
        medians = (
            frame.groupby("auto_state_level", observed=True)[list(TARGET_COMPONENTS)]
            .median()
            .reindex(range(5))
        )
        monotone = bool(
            medians.notna().all().all()
            and all(
                np.all(np.diff(medians[column]) >= 0) for column in TARGET_COMPONENTS
            )
        )
        fold2_station_evidence[station_name] = {
            "missing_levels": [
                WARNING_COLORS[i] for i in range(5) if counts.loc[i] == 0
            ],
            "raw_outcome_medians_monotone": monotone,
        }
    advisories.append(
        {
            "name": "fold2_station_level_and_order_diagnostic",
            "triggered": any(
                record["missing_levels"] or not record["raw_outcome_medians_monotone"]
                for record in fold2_station_evidence.values()
            ),
            "blocking": False,
            "evidence": fold2_station_evidence,
        }
    )
    support_evidence = {}
    limited = False
    for fold in (1, 2):
        station_counts = _counts(station, fold=fold)
        site_counts = _counts(site, fold=fold)
        support_evidence[str(fold)] = {
            "station": {
                WARNING_COLORS[i]: int(station_counts.loc[i]) for i in range(5)
            },
            "site": {WARNING_COLORS[i]: int(site_counts.loc[i]) for i in range(5)},
        }
        limited |= bool(
            (station_counts < min_class_support_advisory).any()
            or (site_counts < min_class_support_advisory).any()
        )
    advisories.extend(
        [
            {
                "name": "limited_class_support",
                "triggered": limited,
                "blocking": False,
                "minimum_support": min_class_support_advisory,
                "evidence": support_evidence,
            },
            {
                "name": "external_repeat_byte_check",
                "triggered": True,
                "blocking": False,
                "status": "not_run_in_labels_only_gate",
            },
        ]
    )
    return {
        "artifact_kind": ARTIFACT_KIND,
        "artifact_status": ARTIFACT_STATUS,
        "formal_warning_output": False,
        "ngboost_trained": False,
        "interpretation": "automatic_future_deformation_state_proxy_not_field_hazard_truth",
        "label_gate_passed": all(record["passed"] for record in gates),
        "fold_roles": {
            "1": "ecdf_taxonomy_and_future_classifier_training",
            "2": "development_evaluation_already_exposed",
            "3": "historical_evaluation_already_exposed_not_confirmatory",
        },
        "gates": gates,
        "advisories": advisories,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _definition_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(records)
    if "max_input_date" in frame:
        frame["max_input_date"] = pd.to_datetime(
            frame["max_input_date"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")
    preferred = [
        "record_type",
        "scope",
        "component",
        "unique_value",
        "value_count",
        "cumulative_count",
        "total_count",
        "cdf",
        "ecdf_side",
        "boundary_index",
        "quantile_probability",
        "boundary_value",
        "quantile_method",
        "fit_fold",
        "max_input_date",
        "boundary_version",
    ]
    return frame.loc[:, [column for column in preferred if column in frame.columns]]


def _load_v1_station(path: Path, manifest_path: Path) -> pd.DataFrame:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise base.AutoStateInputError(
            f"Cannot read v1 manifest: {manifest_path}"
        ) from exc
    record = manifest.get("outputs", {}).get("station_labels")
    if not isinstance(record, dict):
        raise base.AutoStateInputError("v1 manifest lacks station_labels output")
    if record.get("path") != base._relative_path(path) or record.get(
        "sha256"
    ) != _sha256(path):
        raise base.AutoStateInputError(
            "v1 station labels fail manifest path/hash validation"
        )
    if manifest.get("formal_warning_output") is not False:
        raise base.AutoStateInputError("v1 source must remain non-formal")
    station = base._read_csv(path, name="v1 station auto labels")
    required = {
        "fold",
        "date",
        "station",
        "target_end_date",
        "label_status",
        "interval_z",
        "velocity_mm_per_day",
        "acceleration_mm_per_day_squared",
        "tangent_angle_degree",
        *TARGET_COMPONENTS,
    }
    base._require_columns(station, required, name="v1 station auto labels")
    expected_rows = manifest.get("row_counts", {}).get("station_output_rows")
    if expected_rows != len(station):
        raise base.AutoStateInputError(
            "v1 station row count disagrees with its manifest"
        )
    station = base._parse_dates(station, ("date",), name="v1 station auto labels")
    station["target_end_date"] = pd.to_datetime(
        station["target_end_date"], errors="coerce"
    )
    station["fold"] = pd.to_numeric(station["fold"], errors="raise").astype(int)
    station["station"] = station["station"].astype("string").str.strip()
    station = station.drop(
        columns=[
            column
            for column in station.columns
            if column
            in {
                "severity",
                "auto_state_level",
                "auto_state_color",
                "boundary_version",
                "artifact_kind",
                "artifact_status",
                "future_acceleration_q90_mm_per_day_squared",
            }
            or column.startswith("standardized_future_")
        ]
    )
    return station


def run_ecdf_auto_state_label_diagnostic(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> base.AutoStateArtifacts:
    """Run the fixed labels-only ECDF challenger and materialize six outputs."""

    config_path = Path(config_path)
    config = _load_config(config_path)
    paths = {
        name: base._resolve_config_path(value, name=f"inputs.{name}")
        for name, value in config["inputs"].items()
    }
    output_dir = base._resolve_config_path(config["output_dir"], name="output_dir")
    horizon_days = int(config["algorithm"]["horizon_days"])
    label_fit_fold = int(config["algorithm"]["label_fit_fold"])

    blocks = base._load_topology(paths["topology"])
    station = _load_v1_station(paths["v1_station_labels"], paths["v1_manifest"])
    fold_boundaries = (
        station.groupby("fold", sort=True)["date"]
        .agg(test_start_date="min", test_end_date="max")
        .reset_index()
    )
    station, definitions = _fit_and_apply_station_ecdfs(
        station, label_fit_fold=label_fit_fold
    )
    fit_station = station.loc[
        station["label_status"].eq("valid") & station["fold"].eq(label_fit_fold)
    ]
    station_boundaries = _quantile_boundaries(fit_station["severity"])
    station["auto_state_level"] = _assign_levels(
        station["severity"], station_boundaries
    ).array
    station["auto_state_color"] = (
        station["auto_state_level"]
        .map(lambda value: pd.NA if pd.isna(value) else WARNING_COLORS[int(value)])
        .astype("string")
    )
    station["boundary_version"] = BOUNDARY_VERSION
    definitions.extend(
        _boundary_records(
            station_boundaries,
            scope="station",
            fit_fold=label_fit_fold,
            max_input_date=fit_station["target_end_date"].max(),
        )
    )

    site_without_levels = _build_site_timeline(station, blocks)
    site_fit = site_without_levels.loc[
        site_without_levels["label_status"].eq("valid")
        & site_without_levels["fold"].eq(label_fit_fold)
    ]
    site_boundaries = _quantile_boundaries(site_fit["site_severity"])
    site = _build_site_timeline(station, blocks, site_boundaries)
    site["boundary_version"] = BOUNDARY_VERSION
    definitions.extend(
        _boundary_records(
            site_boundaries,
            scope="site",
            fit_fold=label_fit_fold,
            max_input_date=site_fit["target_end_date"].max(),
        )
    )
    definition = _definition_frame(definitions)
    gate = _build_gate(
        station,
        site,
        definition,
        fold_boundaries,
        station_boundaries,
        site_boundaries,
        horizon_days=horizon_days,
        min_class_support_advisory=int(config["gates"]["min_class_support_advisory"]),
    )

    metadata = {
        "artifact_kind": ARTIFACT_KIND,
        "artifact_status": ARTIFACT_STATUS,
        "case": "ootang",
        "formal_warning_output": False,
    }
    for key, value in metadata.items():
        station[key] = value
        site[key] = value
    station["auto_state_level"] = station["auto_state_level"].astype("Int64")
    site["auto_state_level"] = site["auto_state_level"].astype("Int64")
    station_rank = {
        station_name: rank for rank, station_name in enumerate(base.OOTANG_STATIONS)
    }
    station["_station_rank"] = station["station"].map(station_rank)
    station = (
        station.sort_values(["fold", "date", "_station_rank"], kind="stable")
        .drop(columns="_station_rank")
        .reset_index(drop=True)
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    targets = {
        name: output_dir / filename for name, filename in config["outputs"].items()
    }
    base._write_csv(station, targets["station_labels"])
    base._write_csv(site, targets["site_labels"])
    base._write_csv(definition, targets["state_definition"])
    base._write_json(gate, targets["gate"])
    base._plot_timeline(station, site, targets["timeline"])

    input_paths = {
        **paths,
        "config": config_path,
        "source_code": Path(__file__),
        "v1_reused_module": Path(base.__file__),
    }
    manifest = {
        **metadata,
        "profile_id": config["profile_id"],
        "profile_version": config["profile_version"],
        "boundary_version": BOUNDARY_VERSION,
        "label_gate_passed": gate["label_gate_passed"],
        "ngboost_trained": False,
        "algorithm": {
            **config["algorithm"],
            "current_acceleration_retained_in_X": True,
            "future_acceleration_used_in_target": False,
            "parameter_search": False,
        },
        "fold_roles": gate["fold_roles"],
        "row_counts": {
            "v1_source_station_rows": len(station),
            "station_output_rows": len(station),
            "station_valid_label_rows": int(station["label_status"].eq("valid").sum()),
            "site_output_rows": len(site),
            "site_valid_label_rows": int(site["label_status"].eq("valid").sum()),
            "state_definition_rows": len(definition),
        },
        "time_protocol": {
            "label_fit_fold": label_fit_fold,
            "labeler_last_anchor_date": fit_station["date"].max().strftime("%Y-%m-%d"),
            "labeler_max_input_date": fit_station["target_end_date"]
            .max()
            .strftime("%Y-%m-%d"),
            "fold_terminal_days_without_target": horizon_days,
        },
        "spatial_blocks": {name: list(stations) for name, stations in blocks.items()},
        "inputs": {
            name: {"path": base._relative_path(path), "sha256": _sha256(path)}
            for name, path in input_paths.items()
        },
        "outputs": {
            name: {"path": base._relative_path(path), "sha256": _sha256(path)}
            for name, path in targets.items()
            if name != "manifest"
        },
        "not_claimed": config["not_claimed"],
    }
    base._write_json(manifest, targets["manifest"])
    return base.AutoStateArtifacts(
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
    artifacts = run_ecdf_auto_state_label_diagnostic(config_path=args.config)
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
    "DEFAULT_CONFIG_PATH",
    "QUANTILE_PROBABILITIES",
    "TARGET_COMPONENTS",
    "run_ecdf_auto_state_label_diagnostic",
]
