"""Audit the provenance and temporal structure of the Outang input series.

This module is deliberately read-only with respect to the source data.  It
does not repair, interpolate, resample, or otherwise replace observations.
The command-line entry point writes versioned diagnostic artifacts only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import posixpath
import re
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SOURCE_CSV = ROOT / "data" / "monitoring_data.csv"
SOURCE_XLSX = ROOT / "data" / "monitoring_data.xlsx"
FEATURE_CSV = ROOT / "data" / "features.csv"
PREDICTION_CSV = ROOT / "figures" / "convlstm" / "forecast_predictions.csv"
OUTPUT_DIR = ROOT / "figures" / "data_lineage"

DATE_COL = "Date"
DISPLACEMENT_COLS = (
    "MJ9/mm",
    "MJ1/mm",
    "MJ3/mm",
    "ATU4/mm",
    "ATU5/mm",
    "ATU3/mm",
    "ATU2/mm",
    "ATU1/mm",
)
GWT_COL = "GWT/m"
FINGERPRINT_COLS = (*DISPLACEMENT_COLS, GWT_COL)
HIGH_PRECISION_COLS = frozenset(
    ("MJ1/mm", "MJ3/mm", "ATU5/mm", "ATU1/mm", GWT_COL)
)
FIVE_DECIMAL_COLS = frozenset(
    ("MJ9/mm", "ATU4/mm", "ATU3/mm", "ATU2/mm")
)
NEGATIVE_CONTROL_COLS = (
    "Rainfall/mm",
    "RWL/m",
    "aveT/℃",
    "minT/℃",
    "maxT/℃",
)

HIGH_PRECISION_D4_TOLERANCE = 1e-9
HIGH_PRECISION_RESIDUAL_TOLERANCE = 1e-9
FIVE_DECIMAL_D4_TOLERANCE = 8e-5
FIVE_DECIMAL_RESIDUAL_TOLERANCE = 1e-5
SOURCE_PAIR_NUMERIC_TOLERANCE = 1e-12

FIGSHARE_SOURCE = {
    "article_id": 28171343,
    "article_doi": "10.6084/m9.figshare.28171343.v1",
    "file_id": 54029702,
    "file_name": "monitoring data.xlsx",
    "expected_md5": "372d1608f46d7fcdb9805568d1c0782a",
}

SPLIT_BOUNDARIES = (
    ("first_model_target", pd.Timestamp("2016-08-06")),
    ("fit_to_calibration", pd.Timestamp("2019-02-03")),
    ("calibration_to_test", pd.Timestamp("2019-09-18")),
)
PREDICTION_SPLITS = (
    ("fit", pd.Timestamp("2016-08-06"), pd.Timestamp("2019-02-02")),
    (
        "calibration",
        pd.Timestamp("2019-02-03"),
        pd.Timestamp("2019-09-17"),
    ),
    ("test", pd.Timestamp("2019-09-18"), pd.Timestamp("2020-06-30")),
)

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CELL_REF = re.compile(r"([A-Z]+)([0-9]+)")


def file_hash(path: Path, algorithm: str = "sha256") -> str:
    """Return a streaming hash for a local artifact."""
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    """Prefer a repository-relative path, falling back to an absolute path."""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def _column_number(reference: str) -> int:
    match = _CELL_REF.fullmatch(reference)
    if match is None:
        raise ValueError(f"非法 XLSX 单元格引用: {reference}")
    number = 0
    for character in match.group(1):
        number = number * 26 + ord(character) - ord("A") + 1
    return number


def _shared_string_text(node: ElementTree.Element) -> str:
    return "".join(
        text.text or ""
        for text in node.iter(f"{{{_MAIN_NS}}}t")
    )


def _first_sheet_metadata(
    archive: ZipFile,
) -> tuple[str, int, int, bool]:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    sheets = workbook.find(f"{{{_MAIN_NS}}}sheets")
    if sheets is None or len(sheets) == 0:
        raise ValueError("XLSX 不含工作表")
    first_sheet = sheets[0]
    hidden_sheets = sum(
        sheet.attrib.get("state", "visible") != "visible"
        for sheet in sheets
    )
    relationship_id = first_sheet.attrib[f"{{{_REL_NS}}}id"]

    relationships = ElementTree.fromstring(
        archive.read("xl/_rels/workbook.xml.rels")
    )
    targets = {
        relation.attrib["Id"]: relation.attrib["Target"]
        for relation in relationships.findall(
            f"{{{_PACKAGE_REL_NS}}}Relationship"
        )
    }
    target = targets[relationship_id]
    sheet_path = posixpath.normpath(posixpath.join("xl", target))

    workbook_properties = workbook.find(f"{{{_MAIN_NS}}}workbookPr")
    uses_1904_epoch = (
        workbook_properties is not None
        and workbook_properties.attrib.get("date1904") in {"1", "true", "True"}
    )
    return sheet_path, len(sheets), hidden_sheets, uses_1904_epoch


def read_simple_xlsx(path: Path) -> tuple[pd.DataFrame, dict]:
    """Read the first worksheet of the versioned source using the stdlib.

    The source workbook is intentionally simple: one rectangular sheet,
    shared-string headers, numeric data, and no formulas.  Rejecting formulas
    avoids silently evaluating or trusting cached spreadsheet results and
    keeps this audit independent of an optional Excel engine.
    """
    with ZipFile(path) as archive:
        shared_strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            shared_strings = [
                _shared_string_text(item)
                for item in root.findall(f"{{{_MAIN_NS}}}si")
            ]

        sheet_path, sheet_count, hidden_sheets, uses_1904_epoch = (
            _first_sheet_metadata(archive)
        )
        sheet = ElementTree.fromstring(archive.read(sheet_path))
        formula_count = len(sheet.findall(f".//{{{_MAIN_NS}}}f"))
        if formula_count:
            raise ValueError("来源 XLSX 含公式，拒绝把缓存值当作原始数值")

        rows = []
        max_column = 0
        for row in sheet.findall(f".//{{{_MAIN_NS}}}sheetData/{{{_MAIN_NS}}}row"):
            parsed = {}
            for cell in row.findall(f"{{{_MAIN_NS}}}c"):
                reference = cell.attrib["r"]
                column = _column_number(reference)
                max_column = max(max_column, column)
                cell_type = cell.attrib.get("t")
                value_node = cell.find(f"{{{_MAIN_NS}}}v")
                inline_node = cell.find(f"{{{_MAIN_NS}}}is")
                if cell_type == "inlineStr" and inline_node is not None:
                    value = _shared_string_text(inline_node)
                elif value_node is None:
                    value = None
                elif cell_type == "s":
                    value = shared_strings[int(value_node.text)]
                elif cell_type == "b":
                    value = value_node.text == "1"
                elif cell_type in {"str", "e"}:
                    value = value_node.text
                else:
                    value = float(value_node.text)
                parsed[column - 1] = value
            rows.append(parsed)

    matrix = [
        [row.get(column) for column in range(max_column)]
        for row in rows
    ]
    if not matrix:
        raise ValueError("来源 XLSX 第一工作表为空")
    frame = pd.DataFrame(matrix[1:], columns=matrix[0])
    if DATE_COL not in frame:
        raise ValueError(f"来源 XLSX 缺少日期列 {DATE_COL}")
    if pd.api.types.is_numeric_dtype(frame[DATE_COL]):
        epoch = pd.Timestamp("1904-01-01") if uses_1904_epoch else pd.Timestamp(
            "1899-12-30"
        )
        frame[DATE_COL] = epoch + pd.to_timedelta(frame[DATE_COL], unit="D")
    else:
        frame[DATE_COL] = pd.to_datetime(frame[DATE_COL], errors="raise")
    for column in frame.columns:
        if column != DATE_COL:
            frame[column] = pd.to_numeric(frame[column], errors="raise")
    metadata = {
        "sheet_count": sheet_count,
        "hidden_sheet_count": hidden_sheets,
        "formula_cell_count": formula_count,
        "uses_1904_date_epoch": uses_1904_epoch,
    }
    return frame, metadata


def load_monitoring_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame = frame.rename(columns=lambda column: str(column).strip())
    frame[DATE_COL] = pd.to_datetime(frame[DATE_COL], errors="raise")
    return frame


def audit_monitoring_frame(frame: pd.DataFrame) -> dict:
    """Check chronology and completeness without imputing any value."""
    expected = {DATE_COL, *FINGERPRINT_COLS, *NEGATIVE_CONTROL_COLS}
    missing_columns = sorted(expected.difference(frame.columns))
    dates = pd.DatetimeIndex(frame[DATE_COL])
    day_steps = dates.to_series(index=None).diff().dropna()
    return {
        "rows": len(frame),
        "columns": len(frame.columns),
        "start_date": dates.min().date().isoformat(),
        "end_date": dates.max().date().isoformat(),
        "missing_required_columns": missing_columns,
        "missing_value_count": int(frame.isna().sum().sum()),
        "duplicate_date_count": int(dates.duplicated().sum()),
        "dates_monotonic_increasing": bool(dates.is_monotonic_increasing),
        "daily_cadence": bool(
            len(day_steps) == max(len(frame) - 1, 0)
            and day_steps.eq(pd.Timedelta(days=1)).all()
        ),
    }


def audit_source_pair(csv_path: Path, xlsx_path: Path) -> dict:
    """Verify that the repository CSV is a faithful copy of the XLSX."""
    csv_frame = load_monitoring_csv(csv_path)
    xlsx_frame, xlsx_metadata = read_simple_xlsx(xlsx_path)
    columns_equal = list(csv_frame.columns) == list(xlsx_frame.columns)
    dates_equal = (
        columns_equal
        and csv_frame[DATE_COL].equals(xlsx_frame[DATE_COL])
    )
    max_absolute_difference = math.inf
    if columns_equal and dates_equal:
        numeric_columns = [
            column for column in csv_frame.columns if column != DATE_COL
        ]
        difference = np.abs(
            csv_frame[numeric_columns].to_numpy(dtype=float)
            - xlsx_frame[numeric_columns].to_numpy(dtype=float)
        )
        max_absolute_difference = float(difference.max(initial=0.0))

    xlsx_md5 = file_hash(xlsx_path, "md5")
    return {
        "csv_path": str(csv_path.relative_to(ROOT)),
        "xlsx_path": str(xlsx_path.relative_to(ROOT)),
        "csv_sha256": file_hash(csv_path),
        "xlsx_sha256": file_hash(xlsx_path),
        "xlsx_md5": xlsx_md5,
        "figshare_expected_md5": FIGSHARE_SOURCE["expected_md5"],
        "xlsx_matches_figshare_release": (
            xlsx_md5 == FIGSHARE_SOURCE["expected_md5"]
        ),
        "csv_shape": [int(value) for value in csv_frame.shape],
        "xlsx_shape": [int(value) for value in xlsx_frame.shape],
        "columns_equal": columns_equal,
        "dates_equal": bool(dates_equal),
        "max_absolute_numeric_difference": max_absolute_difference,
        "numeric_tolerance": SOURCE_PAIR_NUMERIC_TOLERANCE,
        "numeric_values_equal_within_tolerance": bool(
            max_absolute_difference <= SOURCE_PAIR_NUMERIC_TOLERANCE
        ),
        "xlsx_structure": xlsx_metadata,
    }


def _column_protocol(column: str) -> dict:
    if column in HIGH_PRECISION_COLS:
        return {
            "role": "fingerprint_target",
            "precision_group": "high_precision",
            "d4_tolerance": HIGH_PRECISION_D4_TOLERANCE,
            "cubic_residual_tolerance": HIGH_PRECISION_RESIDUAL_TOLERANCE,
        }
    if column in FIVE_DECIMAL_COLS:
        return {
            "role": "fingerprint_target",
            "precision_group": "five_decimal_rounded",
            "d4_tolerance": FIVE_DECIMAL_D4_TOLERANCE,
            "cubic_residual_tolerance": FIVE_DECIMAL_RESIDUAL_TOLERANCE,
        }
    if column in NEGATIVE_CONTROL_COLS:
        return {
            "role": "negative_control",
            "precision_group": "negative_control",
            "d4_tolerance": HIGH_PRECISION_D4_TOLERANCE,
            "cubic_residual_tolerance": HIGH_PRECISION_RESIDUAL_TOLERANCE,
        }
    raise ValueError(f"未配置的审计列: {column}")


def _polynomial_residuals(values: np.ndarray, degree: int) -> np.ndarray:
    x = np.arange(len(values), dtype=float)
    coefficients = np.polynomial.polynomial.polyfit(x, values, degree)
    fitted = np.polynomial.polynomial.polyval(x, coefficients)
    return values - fitted


def temporal_polynomial_fingerprint(
    frame: pd.DataFrame,
    columns: tuple[str, ...] = (*FINGERPRINT_COLS, *NEGATIVE_CONTROL_COLS),
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return month-level and column-level polynomial fingerprint tables."""
    working = frame.copy()
    working[DATE_COL] = pd.to_datetime(working[DATE_COL], errors="raise")
    working["_month"] = working[DATE_COL].dt.to_period("M")
    monthly_rows = []

    for month, month_frame in working.groupby("_month", sort=True):
        for column in columns:
            values = month_frame[column].to_numpy(dtype=float)
            protocol = _column_protocol(column)
            fourth_difference = np.diff(values, n=4)
            cubic_residual = _polynomial_residuals(values, degree=3)
            quadratic_residual = _polynomial_residuals(values, degree=2)
            max_d4 = float(np.abs(fourth_difference).max(initial=0.0))
            max_cubic_residual = float(
                np.abs(cubic_residual).max(initial=0.0)
            )
            monthly_rows.append({
                "month": str(month),
                "column": column,
                **protocol,
                "n_days": len(values),
                "median_abs_d4": float(np.median(np.abs(fourth_difference))),
                "max_abs_d4": max_d4,
                "cubic_rmse": float(np.sqrt(np.mean(cubic_residual ** 2))),
                "max_abs_cubic_residual": max_cubic_residual,
                "quadratic_rmse": float(
                    np.sqrt(np.mean(quadratic_residual ** 2))
                ),
                "month_passes_cubic_fingerprint": bool(
                    max_d4 <= protocol["d4_tolerance"]
                    and max_cubic_residual
                    <= protocol["cubic_residual_tolerance"]
                ),
            })

    monthly = pd.DataFrame(monthly_rows)
    boundary_statistics = _boundary_difference_statistics(working, columns)
    summary_rows = []
    for column, group in monthly.groupby("column", sort=False):
        boundary = boundary_statistics[column]
        summary_rows.append({
            "column": column,
            "role": group["role"].iloc[0],
            "precision_group": group["precision_group"].iloc[0],
            "months_total": len(group),
            "months_passing_cubic_fingerprint": int(
                group["month_passes_cubic_fingerprint"].sum()
            ),
            "all_months_pass_cubic_fingerprint": bool(
                group["month_passes_cubic_fingerprint"].all()
            ),
            "monthly_cubic_rmse_median": float(group["cubic_rmse"].median()),
            "monthly_cubic_max_residual_global": float(
                group["max_abs_cubic_residual"].max()
            ),
            "monthly_quadratic_rmse_median": float(
                group["quadratic_rmse"].median()
            ),
            **boundary,
        })
    return monthly, pd.DataFrame(summary_rows)


def _boundary_difference_statistics(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
) -> dict[str, dict]:
    dates = pd.DatetimeIndex(frame[DATE_COL])
    starts = dates[:-4]
    ends = dates[4:]
    crosses_month = starts.to_period("M") != ends.to_period("M")
    statistics = {}
    for column in columns:
        protocol = _column_protocol(column)
        fourth_difference = np.abs(
            np.diff(frame[column].to_numpy(dtype=float), n=4)
        )
        exceeds = fourth_difference > protocol["d4_tolerance"]
        endpoint_days = sorted(
            {
                int(day)
                for day in ends.day[crosses_month & exceeds]
            }
        )
        statistics[column] = {
            "within_month_d4_windows": int((~crosses_month).sum()),
            "cross_month_d4_windows": int(crosses_month.sum()),
            "within_month_d4_exceedances": int(
                (exceeds & ~crosses_month).sum()
            ),
            "cross_month_d4_exceedances": int(
                (exceeds & crosses_month).sum()
            ),
            "all_d4_exceedances_cross_month": bool(
                not np.any(exceeds & ~crosses_month)
            ),
            "cross_month_exceedance_endpoint_days": ",".join(
                str(day) for day in endpoint_days
            ),
        }
    return statistics


def audit_split_boundary_months(
    frame: pd.DataFrame,
    columns: tuple[str, ...] = FINGERPRINT_COLS,
) -> pd.DataFrame:
    """Check whether one monthly cubic segment crosses each model boundary."""
    working = frame.copy()
    working[DATE_COL] = pd.to_datetime(working[DATE_COL], errors="raise")
    rows = []
    for boundary_name, boundary_date in SPLIT_BOUNDARIES:
        month = boundary_date.to_period("M")
        month_frame = working.loc[
            working[DATE_COL].dt.to_period("M").eq(month)
        ]
        for column in columns:
            protocol = _column_protocol(column)
            values = month_frame[column].to_numpy(dtype=float)
            residual = _polynomial_residuals(values, degree=3)
            max_residual = float(np.abs(residual).max(initial=0.0))
            rows.append({
                "boundary": boundary_name,
                "boundary_date": boundary_date.date().isoformat(),
                "month": str(month),
                "column": column,
                "precision_group": protocol["precision_group"],
                "rows_before_boundary_in_month": int(
                    month_frame[DATE_COL].lt(boundary_date).sum()
                ),
                "rows_on_or_after_boundary_in_month": int(
                    month_frame[DATE_COL].ge(boundary_date).sum()
                ),
                "max_abs_cubic_residual": max_residual,
                "cubic_residual_tolerance": protocol[
                    "cubic_residual_tolerance"
                ],
                "same_monthly_cubic_segment_across_boundary": bool(
                    max_residual
                    <= protocol["cubic_residual_tolerance"]
                ),
            })
    return pd.DataFrame(rows)


def audit_split_cross_boundary_predictability(
    frame: pd.DataFrame,
    columns: tuple[str, ...] = FINGERPRINT_COLS,
) -> pd.DataFrame:
    """Quantify algebraic predictability across each within-month boundary.

    A cubic needs at least four rows.  When fewer than four rows precede a
    boundary, the later side is fitted and the earlier side is backcast.  The
    direction is explicit so this diagnostic is not misread as a forecasting
    experiment.
    """
    working = frame.copy()
    working[DATE_COL] = pd.to_datetime(working[DATE_COL], errors="raise")
    rows = []
    for boundary_name, boundary_date in SPLIT_BOUNDARIES:
        month = boundary_date.to_period("M")
        month_frame = working.loc[
            working[DATE_COL].dt.to_period("M").eq(month)
        ].reset_index(drop=True)
        before = month_frame[DATE_COL].lt(boundary_date).to_numpy()
        after = ~before
        if before.sum() >= 4:
            fit_mask = before
            evaluation_mask = after
            direction = "pre_boundary_to_on_or_after_boundary"
        elif after.sum() >= 4:
            fit_mask = after
            evaluation_mask = before
            direction = "on_or_after_boundary_to_pre_boundary_backcast"
        else:
            raise ValueError(
                f"{boundary_name} 两侧都不足 4 行，无法作三次段审计"
            )
        x = np.arange(len(month_frame), dtype=float)
        for column in columns:
            values = month_frame[column].to_numpy(dtype=float)
            coefficients = np.polynomial.polynomial.polyfit(
                x[fit_mask],
                values[fit_mask],
                deg=3,
            )
            predicted = np.polynomial.polynomial.polyval(
                x[evaluation_mask],
                coefficients,
            )
            error = predicted - values[evaluation_mask]
            month_range = float(np.ptp(values))
            max_error = float(np.abs(error).max(initial=0.0))
            rows.append({
                "boundary": boundary_name,
                "boundary_date": boundary_date.date().isoformat(),
                "month": str(month),
                "column": column,
                "precision_group": _column_protocol(column)[
                    "precision_group"
                ],
                "direction": direction,
                "fit_rows": int(fit_mask.sum()),
                "evaluation_rows": int(evaluation_mask.sum()),
                "mean_absolute_error": float(np.abs(error).mean()),
                "max_absolute_error": max_error,
                "month_value_range": month_range,
                "max_error_fraction_of_month_range": (
                    max_error / month_range if month_range > 0 else np.nan
                ),
                "interpretation": (
                    "algebraic_dependence_diagnostic_not_forecast_evaluation"
                ),
            })
    return pd.DataFrame(rows)


def audit_prediction_date_alignment(
    features_path: Path,
    predictions_path: Path,
) -> tuple[dict, pd.DataFrame]:
    """Verify the complete frozen prediction schedule and dated values."""
    features = pd.read_csv(features_path)
    features[DATE_COL] = pd.to_datetime(features[DATE_COL], errors="raise")
    predictions = pd.read_csv(predictions_path)
    predictions["date"] = pd.to_datetime(predictions["date"], errors="raise")
    for column in ("actual", "persistence"):
        predictions[column] = pd.to_numeric(
            predictions[column],
            errors="coerce",
        )
    actual_finite = np.isfinite(predictions["actual"].to_numpy(dtype=float))
    persistence_finite = np.isfinite(
        predictions["persistence"].to_numpy(dtype=float)
    )
    prediction_values_finite = bool(
        actual_finite.all() and persistence_finite.all()
    )
    expected_stations = sorted(
        column.removesuffix("/mm") for column in DISPLACEMENT_COLS
    )
    observed_stations = sorted(predictions["station"].unique())

    feature_long = []
    for station in expected_stations:
        column = f"{station}_disp"
        if column not in features:
            raise ValueError(f"特征表缺少位移列 {column}")
        station_frame = features[[DATE_COL, column]].rename(
            columns={DATE_COL: "date", column: "expected_actual"}
        )
        station_frame["station"] = station
        feature_long.append(station_frame)
    expected = pd.concat(feature_long, ignore_index=True)
    expected["expected_persistence"] = expected.groupby(
        "station", sort=False
    )["expected_actual"].shift(1)

    expected_schedule_parts = []
    for split, start_date, end_date in PREDICTION_SPLITS:
        dates = pd.date_range(start_date, end_date, freq="D")
        schedule = pd.MultiIndex.from_product(
            [dates, expected_stations],
            names=["date", "station"],
        ).to_frame(index=False)
        schedule["split"] = split
        expected_schedule_parts.append(schedule)
    expected_schedule = pd.concat(expected_schedule_parts, ignore_index=True)
    expected_keys = set(
        expected_schedule[["date", "station", "split"]]
        .itertuples(index=False, name=None)
    )
    observed_keys = set(
        predictions[["date", "station", "split"]]
        .itertuples(index=False, name=None)
    )
    missing_keys = expected_keys.difference(observed_keys)
    extra_keys = observed_keys.difference(expected_keys)

    merged = predictions.merge(
        expected,
        on=["date", "station"],
        how="left",
        validate="many_to_one",
    )
    if merged[["expected_actual", "expected_persistence"]].isna().any().any():
        raise ValueError("预测表日期/测点无法完整映射到特征表")
    actual_error = np.abs(merged["actual"] - merged["expected_actual"])
    persistence_error = np.abs(
        merged["persistence"] - merged["expected_persistence"]
    )

    def strict_max_error(values: pd.Series) -> float | None:
        array = values.to_numpy(dtype=float)
        if not np.isfinite(array).all():
            return None
        return float(array.max())

    split_rows = []
    for split, group in merged.groupby("split", sort=False):
        split_rows.append({
            "split": split,
            "start_date": group["date"].min().date().isoformat(),
            "end_date": group["date"].max().date().isoformat(),
            "n_dates": int(group["date"].nunique()),
            "n_stations": int(group["station"].nunique()),
            "n_rows": len(group),
            "max_abs_actual_alignment_error": strict_max_error(
                np.abs(group["actual"] - group["expected_actual"])
            ),
            "max_abs_persistence_alignment_error": strict_max_error(
                np.abs(
                    group["persistence"] - group["expected_persistence"]
                )
            ),
        })
    duplicate_pairs = int(
        predictions.duplicated(["date", "station"], keep=False).sum()
    )
    split_schedule_passed = (
        not missing_keys
        and not extra_keys
        and len(predictions) == len(expected_schedule)
    )
    max_actual_error = strict_max_error(actual_error)
    max_persistence_error = strict_max_error(persistence_error)
    summary = {
        "feature_path": display_path(features_path),
        "prediction_path": display_path(predictions_path),
        "prediction_rows": len(predictions),
        "expected_prediction_rows": len(expected_schedule),
        "stations": observed_stations,
        "expected_stations": expected_stations,
        "station_set_matches": observed_stations == expected_stations,
        "prediction_values_finite": prediction_values_finite,
        "non_finite_actual_values": int((~actual_finite).sum()),
        "non_finite_persistence_values": int(
            (~persistence_finite).sum()
        ),
        "duplicate_date_station_rows": duplicate_pairs,
        "missing_expected_date_station_split_keys": len(missing_keys),
        "unexpected_date_station_split_keys": len(extra_keys),
        "frozen_split_schedule_passed": split_schedule_passed,
        "max_abs_actual_alignment_error": max_actual_error,
        "max_abs_persistence_alignment_error": max_persistence_error,
        "alignment_passed": bool(
            duplicate_pairs == 0
            and observed_stations == expected_stations
            and split_schedule_passed
            and prediction_values_finite
            and max_actual_error == 0
            and max_persistence_error == 0
        ),
    }
    return summary, pd.DataFrame(split_rows)


def _artifact_record(path: Path) -> dict:
    return {
        "path": display_path(path),
        "size_bytes": path.stat().st_size,
        "sha256": file_hash(path),
    }


def run_audit(
    *,
    csv_path: Path = SOURCE_CSV,
    xlsx_path: Path = SOURCE_XLSX,
    features_path: Path = FEATURE_CSV,
    predictions_path: Path = PREDICTION_CSV,
    output_dir: Path = OUTPUT_DIR,
) -> dict:
    """Run the complete read-only audit and write diagnostic artifacts."""
    frame = load_monitoring_csv(csv_path)
    source_pair = audit_source_pair(csv_path, xlsx_path)
    frame_audit = audit_monitoring_frame(frame)
    monthly, column_summary = temporal_polynomial_fingerprint(frame)
    split_boundaries = audit_split_boundary_months(frame)
    cross_boundary = audit_split_cross_boundary_predictability(frame)
    alignment, split_summary = audit_prediction_date_alignment(
        features_path,
        predictions_path,
    )

    fingerprint_targets = column_summary.loc[
        column_summary["role"].eq("fingerprint_target")
    ]
    negative_controls = column_summary.loc[
        column_summary["role"].eq("negative_control")
    ]
    fingerprint_is_strong = bool(
        fingerprint_targets["all_months_pass_cubic_fingerprint"].all()
        and not negative_controls["all_months_pass_cubic_fingerprint"].any()
        and fingerprint_targets["all_d4_exceedances_cross_month"].all()
        and fingerprint_targets["cross_month_d4_exceedances"].gt(0).all()
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    monthly_path = output_dir / "ootang_monthly_polynomial_fingerprint.csv"
    summary_path = output_dir / "ootang_column_fingerprint_summary.csv"
    boundaries_path = output_dir / "ootang_split_boundary_audit.csv"
    cross_boundary_path = (
        output_dir / "ootang_split_cross_boundary_predictability.csv"
    )
    alignment_path = output_dir / "ootang_prediction_alignment_summary.csv"
    manifest_path = output_dir / "ootang_data_lineage_manifest.json"

    monthly.to_csv(monthly_path, index=False)
    column_summary.to_csv(summary_path, index=False)
    split_boundaries.to_csv(boundaries_path, index=False)
    cross_boundary.to_csv(cross_boundary_path, index=False)
    split_summary.to_csv(alignment_path, index=False)

    manifest = {
        "schema_version": "ootang_data_lineage_audit_v1",
        "audit_implementation": {
            "audit_code_path": str(Path(__file__).resolve().relative_to(ROOT)),
            "audit_code_sha256": file_hash(Path(__file__).resolve()),
        },
        "audit_scope": "ootang_only",
        "formal_warning_output": False,
        "vajont_used": False,
        "test_rows_audited_for_data_provenance": True,
        "model_selection_usage": {
            "status": "protocol_statement_not_verified_by_this_audit",
            "declared_test_rows_used_for_model_selection": 0,
        },
        "published_source": FIGSHARE_SOURCE,
        "source_pair": source_pair,
        "downstream_input_hashes": {
            "features_path": str(features_path.relative_to(ROOT)),
            "features_sha256": file_hash(features_path),
            "predictions_path": str(predictions_path.relative_to(ROOT)),
            "predictions_sha256": file_hash(predictions_path),
        },
        "monitoring_frame": frame_audit,
        "prediction_alignment": alignment,
        "numeric_fingerprint": {
            "status": "strong" if fingerprint_is_strong else "not_confirmed",
            "structure": (
                "natural_month_piecewise_cubic"
                if fingerprint_is_strong
                else "not_confirmed"
            ),
            "target_columns": list(FINGERPRINT_COLS),
            "negative_control_columns": list(NEGATIVE_CONTROL_COLS),
            "months": int(monthly["month"].nunique()),
            "all_split_boundary_months_share_cubic_segment": bool(
                split_boundaries[
                    "same_monthly_cubic_segment_across_boundary"
                ].all()
            ),
            "cross_boundary_predictability_role": (
                "algebraic_dependence_diagnostic_not_forecast_evaluation"
            ),
        },
        "lineage_status": {
            "released_series_role": "materialized_daily_modeling_series",
            "exact_generation_algorithm": "unresolved",
            "original_observation_anchors": (
                "not_available_in_audited_release"
            ),
            "future_information_usage": "unknown",
            "independent_raw_daily_gnss_claim": "not_supported",
        },
        "data_gate": {
            "status": "blocked",
            "blocks": [
                "confirmatory_daily_forecast_claims",
                "new_neural_ablation_as_bias_explanation",
                "formal_v0_and_derivative_thresholds",
                "formal_four_indicator_fusion",
                "formal_warning_output",
            ],
            "does_not_block": [
                "read_only_lineage_audit",
                "engineering_checks_on_materialized_series",
                "operational_draft_not_formal",
            ],
            "required_to_reopen": [
                "original_observation_dates_and_values",
                "documented_daily_series_generation_method",
                "evidence_whether_monthly_processing_used_future_anchors",
                "documented_qc_reference_epoch_and_station_mapping",
            ],
        },
    }
    manifest["artifacts"] = [
        _artifact_record(path)
        for path in (
            monthly_path,
            summary_path,
            boundaries_path,
            cross_boundary_path,
            alignment_path,
        )
    ]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="审计藕塘公开输入的来源、月内数值结构和预测日期对齐"
    )
    parser.add_argument("--csv", type=Path, default=SOURCE_CSV)
    parser.add_argument("--xlsx", type=Path, default=SOURCE_XLSX)
    parser.add_argument("--features", type=Path, default=FEATURE_CSV)
    parser.add_argument("--predictions", type=Path, default=PREDICTION_CSV)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = run_audit(
        csv_path=args.csv,
        xlsx_path=args.xlsx,
        features_path=args.features,
        predictions_path=args.predictions,
        output_dir=args.output_dir,
    )
    print(
        "[data-lineage] "
        f"fingerprint={manifest['numeric_fingerprint']['status']} "
        f"gate={manifest['data_gate']['status']} "
        f"output={args.output_dir}"
    )


if __name__ == "__main__":
    main()
