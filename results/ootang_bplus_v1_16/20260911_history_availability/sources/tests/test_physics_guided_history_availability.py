"""Synthetic calendar, left-boundary, and future-row isolation checks."""

import csv
from datetime import date, timedelta
import io
from pathlib import Path
import tempfile
import unittest
from xml.sax.saxutils import escape
from zipfile import ZipFile

import numpy as np

from physics_guided_history_availability.core import (
    COLUMNS,
    NS,
    START,
    materialized_history,
    read_csv_prefix,
    read_xlsx_prefix,
)


def example_rows(days=9):
    return [
        [
            (START + timedelta(days=i)).isoformat(),
            i * i,
            10 * i,
            7 + i,
            100 - i,
            i % 3,
            150 + i,
        ]
        for i in range(days)
    ]


def csv_stream(rows, mentor=False):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["Date", *[c.split("/")[0] if mentor else c for c in COLUMNS]])
    writer.writerows(rows)
    stream.seek(0)
    return stream


def workbook_fixture(
    path, rows, poison_future=False, missing_cell=False, formula=False
):
    """Minimal XML fixture, independent of the real workbook and its contents."""
    ns = NS[1:-1]
    headers = ["Date", *COLUMNS]
    xml_rows = [
        '<row r="1">'
        + "".join(
            f'<c r="{chr(65 + j)}1" t="inlineStr"><is><t>{escape(h)}</t></is></c>'
            for j, h in enumerate(headers)
        )
        + "</row>"
    ]
    for i, row in enumerate(rows):
        values = [(date.fromisoformat(row[0]) - date(1899, 12, 30)).days, *row[1:]]
        cells = []
        for j, value in enumerate(values):
            if missing_cell and i == 3 and j == 1:
                continue
            if poison_future and i >= 6:
                value = "future_must_not_be_converted"
            f = "<f>1+2</f>" if formula and i == 6 and j == 1 else ""
            cells.append(f'<c r="{chr(65 + j)}{i + 2}">{f}<v>{value}</v></c>')
        xml_rows.append(f'<row r="{i + 2}">' + "".join(cells) + "</row>")
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{ns}" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Synthetic history" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            "<Relationships "
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{ns}"><sheetData>'
            + "".join(xml_rows)
            + "</sheetData></worksheet>",
        )


class HistoryAvailabilityTests(unittest.TestCase):
    def test_history_last_day_and_difference_left_endpoint(self):
        history = materialized_history(csv_stream(example_rows()), 6, 3)
        np.testing.assert_array_equal(
            history["dates"], ["2016-07-04", "2016-07-05", "2016-07-06"]
        )
        np.testing.assert_array_equal(
            history["u"], [[9, 30, 10, 97], [16, 40, 11, 96], [25, 50, 12, 95]]
        )
        np.testing.assert_array_equal(
            history["du"], [[5, 10, 1, -1], [7, 10, 1, -1], [9, 10, 1, -1]]
        )
        self.assertEqual(history["metadata"]["difference_left_index"], 2)
        self.assertEqual(history["metadata"]["last_available_index"], 5)
        self.assertEqual(
            history["metadata"]["raw_observation_as_of_verified"], "unknown"
        )

    def test_csv_does_not_visit_first_forecast_row(self):
        class Guarded(io.StringIO):
            count = 0

            def __next__(self):
                if self.count >= 7:  # Header plus exactly six past data rows.
                    raise AssertionError("Visited the forecast row")
                self.count += 1
                return super().__next__()

        rows = example_rows()
        rows[6:] = [["bad_future_date", *["future_must_not_parse"] * 6]]
        source = Guarded(csv_stream(rows).getvalue())
        history = materialized_history(source, 6, 3)
        self.assertEqual(source.count, 7)
        self.assertEqual(history["u"][-1, 0], 25)

    def test_missing_or_short_calendar_rejected(self):
        for rows in (example_rows()[:5], example_rows()[:2] + example_rows()[3:]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                materialized_history(csv_stream(rows), 6, 3)

    def test_duplicate_and_unordered_dates_rejected(self):
        for unordered in (False, True):
            rows = example_rows()
            if unordered:
                rows[1], rows[2] = rows[2], rows[1]
            else:
                rows[2][0] = rows[1][0]
            with self.subTest(unordered=unordered), self.assertRaises(ValueError):
                materialized_history(csv_stream(rows), 6, 3)

    def test_invalid_historical_values_rejected(self):
        for bad in ("", "nan", "inf", "-inf", "missing"):
            rows = example_rows()
            rows[2][1] = bad  # The left endpoint outside the returned u window.
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                materialized_history(csv_stream(rows), 6, 3)

    def test_origin_and_history_bounds_rejected(self):
        for origin, history in [
            (0, 3),
            (3, 3),
            (613, 30),
            (6.0, 3),
            (True, 1),
            (6, 0),
            (6, 2.5),
        ]:
            with (
                self.subTest(origin=origin, history=history),
                self.assertRaises(ValueError),
            ):
                materialized_history(csv_stream(example_rows()), origin, history)

    def test_mentor_column_mapping(self):
        reference = read_csv_prefix(csv_stream(example_rows()), 6)
        mentor = read_csv_prefix(
            csv_stream(example_rows(), mentor=True), 6, mentor=True
        )
        np.testing.assert_array_equal(reference["values"], mentor["values"])
        with self.assertRaises(ValueError):
            read_csv_prefix(csv_stream(example_rows(), mentor=True), 6)

    def test_xlsx_prefix_matches_independent_csv_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.xlsx"
            workbook_fixture(path, example_rows())
            actual = read_xlsx_prefix(path, 6)
        expected = read_csv_prefix(csv_stream(example_rows()), 6)
        np.testing.assert_array_equal(actual["values"], expected["values"])
        np.testing.assert_array_equal(actual["dates"], expected["dates"])
        self.assertEqual(actual["workbook"]["sheet_name"], "Synthetic history")
        self.assertEqual(actual["workbook"]["converted_data_rows"], 6)
        self.assertEqual(actual["workbook"]["data_rows_from_structure"], 9)

    def test_xlsx_future_numeric_cells_are_never_converted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.xlsx"
            workbook_fixture(path, example_rows(), poison_future=True)
            actual = read_xlsx_prefix(path, 6)
        np.testing.assert_array_equal(actual["values"][:, 0], [0, 1, 4, 9, 16, 25])

    def test_xlsx_missing_past_cells_and_formula_caches_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.xlsx"
            for option in ("missing_cell", "formula"):
                workbook_fixture(path, example_rows(), **{option: True})
                with self.subTest(option=option), self.assertRaises(ValueError):
                    read_xlsx_prefix(path, 6)


if __name__ == "__main__":
    unittest.main()
