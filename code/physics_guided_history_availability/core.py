"""Bounded readers and a history interface; no interpolation or model calls."""

import csv
from datetime import date, timedelta
from itertools import islice
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import numpy as np

from convlstm.data_lineage_audit import (
    _MAIN_NS,
    _column_number,
    _first_sheet_metadata,
    _shared_string_text,
)

POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")
COLUMNS = tuple(p + "/mm" for p in POINTS) + ("Rainfall/mm", "RWL/m")
START = date(2016, 7, 1)
SOURCE_ROLE = "materialized_daily_modeling_series"
UNKNOWN = "unknown"
NS = "{" + _MAIN_NS + "}"


def prefix_days(days):
    if type(days) is not int or not 1 <= days <= 612:
        raise ValueError("Only integer prefixes within the first 612 days are allowed")


def validate_prefix(dates, values, days):
    expected = [(START + timedelta(days=i)).isoformat() for i in range(days)]
    values = np.asarray(values, dtype=float)
    if dates != expected:
        raise ValueError("Missing, duplicate, unordered, or non-prefix daily dates")
    if values.shape != (days, len(COLUMNS)) or not np.isfinite(values).all():
        raise ValueError("Missing or nonfinite prefix values; no filling is allowed")
    return dict(dates=np.array(dates), values=values)


def read_csv_prefix(source, days, *, mentor=False):
    """Convert exactly the first `days` records, never a subsequent data row."""
    prefix_days(days)
    if not hasattr(source, "read"):
        with Path(source).open(encoding="utf-8-sig", newline="") as stream:
            return read_csv_prefix(stream, days, mentor=mentor)
    reader = csv.DictReader(source)
    header = reader.fieldnames or []
    names = [c.split("/")[0] for c in COLUMNS] if mentor else list(COLUMNS)
    if len(set(header)) != len(header) or not {"Date", *names}.issubset(header):
        raise ValueError("Missing or duplicate source columns")
    dates, values = [], []
    for row in islice(reader, days):
        if None in row or any(row.get(c) is None for c in ("Date", *names)):
            raise ValueError("Malformed source row")
        dates.append(row["Date"])
        values.append([float(row[c]) for c in names])
    return dict(**validate_prefix(dates, values, days), source_columns=header)


def read_xlsx_prefix(path, days):
    """Inspect all structure, but convert only Date and six columns up to `days`."""
    prefix_days(days)
    with ZipFile(path) as archive:
        sheet_path, count, hidden, epoch1904 = _first_sheet_metadata(archive)
        if count != 1 or hidden:
            raise ValueError("The source must have exactly one visible worksheet")
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheet_name = workbook.find(NS + "sheets")[0].attrib["name"]
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = [
                _shared_string_text(item)
                for item in ET.fromstring(archive.read("xl/sharedStrings.xml"))
            ]
        sheet = ET.fromstring(archive.read(sheet_path))
    formula_count = len(sheet.findall(".//" + NS + "f"))
    if formula_count:
        raise ValueError("Formula caches cannot be treated as source observations")
    rows = sheet.findall(NS + "sheetData/" + NS + "row")
    prefix = [r for r in rows if int(r.attrib["r"]) <= days + 1]
    if [int(r.attrib["r"]) for r in prefix] != list(range(1, days + 2)):
        raise ValueError("Missing or duplicated worksheet prefix rows")

    def cell_value(cell):
        kind, node = cell.attrib.get("t", "n"), cell.find(NS + "v")
        if kind == "inlineStr":
            return _shared_string_text(cell)
        if node is None or node.text is None:
            raise ValueError("Missing worksheet cell value")
        if kind == "s":
            return strings[int(node.text)]
        if kind == "n":
            return float(node.text)
        raise ValueError("Unsupported source cell type: " + kind)

    def cells_by_column(row):
        cells = row.findall(NS + "c")
        result = {}
        for cell in cells:
            ref = cell.attrib["r"]
            col = _column_number(ref)
            if col in result or int(ref.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")) != int(
                row.attrib["r"]
            ):
                raise ValueError("Duplicate or misplaced worksheet cell")
            result[col] = cell
        return result

    header_cells = cells_by_column(prefix[0])
    if set(header_cells) != set(range(1, len(header_cells) + 1)):
        raise ValueError("Nonrectangular worksheet header")
    header = [cell_value(header_cells[c]) for c in sorted(header_cells)]
    if len(set(header)) != len(header) or not {"Date", *COLUMNS}.issubset(header):
        raise ValueError("Missing or duplicate worksheet columns")
    selected = [header.index(c) + 1 for c in ("Date", *COLUMNS)]
    dates, values = [], []
    epoch = date(1904, 1, 1) if epoch1904 else date(1899, 12, 30)
    for row in prefix[1:]:
        cells = cells_by_column(row)
        if not set(selected).issubset(cells):
            raise ValueError("Missing selected worksheet cell")
        # No cell_value calls are made on a row beyond the registered prefix.
        parsed = [cell_value(cells[c]) for c in selected]
        serial = parsed[0]
        if (
            not isinstance(serial, float)
            or not np.isfinite(serial)
            or not serial.is_integer()
        ):
            raise ValueError("Worksheet dates must be finite integer day serials")
        dates.append((epoch + timedelta(days=int(serial))).isoformat())
        values.append(parsed[1:])
    return dict(
        **validate_prefix(dates, values, days),
        source_columns=header,
        workbook=dict(
            sheet_name=sheet_name,
            sheet_path=sheet_path,
            sheet_count=count,
            hidden_sheet_count=hidden,
            formula_cell_count=formula_count,
            uses_1904_date_epoch=epoch1904,
            data_rows_from_structure=len(rows) - 1,
            columns_from_header=len(header),
            converted_data_rows=days,
            converted_numeric_columns=list(COLUMNS),
        ),
    )


def materialized_history(source, origin, history_days=30):
    """Return released-series history, without certifying raw as-of availability.

    Index `origin` is the first forecast day. Its row is never read here.
    The first difference uses one additional past day, at origin-history_days-1.
    """
    prefix_days(origin)
    if type(history_days) is not int or not 1 <= history_days < origin:
        raise ValueError(
            "History and its difference left endpoint need a longer prefix"
        )
    prefix = read_csv_prefix(source, origin)
    start = origin - history_days
    u = prefix["values"][:, :4]
    return dict(
        dates=prefix["dates"][start:].copy(),
        u=u[start:].copy(),
        du=(u[start:] - u[start - 1 : -1]).copy(),
        metadata=dict(
            origin=origin,
            source_role=SOURCE_ROLE,
            raw_observation_as_of_verified=UNKNOWN,
            released_prefix_constructible=True,
            source_rows_read=origin,
            history_days=history_days,
            history_start_index=start,
            history_end_index_exclusive=origin,
            difference_left_index=start - 1,
            last_available_index=origin - 1,
            point_order=list(POINTS),
            u_unit="mm",
            du_unit="mm/day; difference of materialized daily values",
        ),
    )
