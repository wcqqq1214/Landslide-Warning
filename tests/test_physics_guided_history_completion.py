"""A failed mentor comparison cannot become a passed equivalence claim."""

import unittest

import numpy as np

from physics_guided_history_completion.core import compare_prefixes


class SourceComparisonTests(unittest.TestCase):
    def sources(self):
        return {
            k: dict(
                dates=np.array(["2016-07-01", "2016-07-02"]), values=np.ones((2, 6))
            )
            for k in ("csv", "xlsx", "mentor")
        }

    def test_mentor_failure_is_reported_at_original_tolerance(self):
        sources = self.sources()
        sources["mentor"]["values"][1, 2] += 5e-12
        report = compare_prefixes(sources, 1e-12)
        self.assertTrue(report["csv_vs_xlsx"]["passed"])
        for pair in ("csv_vs_mentor", "xlsx_vs_mentor"):
            self.assertFalse(report[pair]["passed"])
            self.assertEqual(report[pair]["above_original_tolerance"], 1)
            self.assertEqual(report[pair]["maximum_example"]["column"], "MJ3/mm")

    def test_published_source_discrepancy_rejected(self):
        sources = self.sources()
        sources["xlsx"]["values"][0, 0] += 5e-12
        with self.assertRaisesRegex(ValueError, "Published CSV/XLSX"):
            compare_prefixes(sources, 1e-12)

    def test_mentor_date_mismatch_cannot_be_ignored(self):
        sources = self.sources()
        sources["mentor"]["dates"][1] = "2016-07-03"
        with self.assertRaisesRegex(ValueError, "calendars"):
            compare_prefixes(sources, 1e-12)

    def test_input_mapping_order_does_not_change_comparison_names(self):
        sources = self.sources()
        report = compare_prefixes(dict(reversed(list(sources.items()))), 1e-12)
        self.assertEqual(
            set(report), {"csv_vs_xlsx", "csv_vs_mentor", "xlsx_vs_mentor"}
        )
        self.assertTrue(all(v["passed"] for v in report.values()))


if __name__ == "__main__":
    unittest.main()
