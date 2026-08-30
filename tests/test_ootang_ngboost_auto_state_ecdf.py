"""Focused contracts for the automatic-state ECDF challenger."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning import ootang_ngboost_auto_state as base  # noqa: E402
from warning import ootang_ngboost_auto_state_ecdf as ecdf  # noqa: E402
from warning.levels import WARNING_COLORS  # noqa: E402


class EcdfAutomaticStateTests(unittest.TestCase):
    def test_right_continuous_ecdf_counts_ties_at_the_query_value(self):
        reference = np.array([1.0, 2.0, 2.0, 4.0])
        query = np.array([0.0, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0])

        result = ecdf._right_continuous_ecdf(query, reference)

        np.testing.assert_allclose(
            result,
            [0.0, 0.25, 0.25, 0.75, 0.75, 1.0, 1.0],
        )

    def test_fixed_quintile_boundaries_assign_the_five_warning_colors(self):
        boundaries = ecdf._quantile_boundaries(np.arange(11, dtype=float))
        values = np.array([0.0, 2.0, 4.0, 6.0, 8.0, 10.0])

        levels = ecdf._assign_levels(values, boundaries)
        colors = levels.map(lambda level: WARNING_COLORS[int(level)]).tolist()

        self.assertEqual(boundaries, (2.0, 4.0, 6.0, 8.0))
        self.assertEqual(levels.tolist(), [0, 1, 2, 3, 4, 4])
        self.assertEqual(
            colors,
            ["green", "blue", "yellow", "orange", "red", "red"],
        )

    def test_site_severity_weights_stations_within_blocks_then_blocks_equally(self):
        severity = {
            "MJ9": 0.0,
            "MJ1": 3.0,
            "MJ3": 6.0,
            "ATU4": 10.0,
            "ATU5": 13.0,
            "ATU3": 16.0,
            "ATU2": 20.0,
            "ATU1": 24.0,
        }
        date = pd.Timestamp("2020-01-01")
        rows = []
        for station, value in severity.items():
            rows.append(
                {
                    "fold": 1,
                    "date": date,
                    "station": station,
                    "label_status": "valid",
                    "target_end_date": date + pd.Timedelta(days=7),
                    "severity": value,
                    ecdf.TARGET_COMPONENTS[0]: 10.0 * value,
                    ecdf.TARGET_COMPONENTS[1]: 100.0 + value,
                }
            )

        site = ecdf._build_site_timeline(
            pd.DataFrame(rows),
            base.SITE_BLOCKS_FALLBACK,
            site_boundaries=(0.0, 5.0, 10.0, 15.0),
        ).iloc[0]

        self.assertAlmostEqual(site["block_O1_severity"], 3.0)
        self.assertAlmostEqual(site["block_O2_severity"], 13.0)
        self.assertAlmostEqual(site["block_O3_severity"], 22.0)
        self.assertAlmostEqual(site["site_severity"], (3.0 + 13.0 + 22.0) / 3.0)
        self.assertNotAlmostEqual(
            site["site_severity"], np.mean(list(severity.values()))
        )
        self.assertAlmostEqual(
            site[f"site_{ecdf.TARGET_COMPONENTS[0]}"],
            10.0 * (3.0 + 13.0 + 22.0) / 3.0,
        )
        self.assertEqual(site["auto_state_level"], 3)
        self.assertEqual(site["auto_state_color"], "orange")


if __name__ == "__main__":
    unittest.main()
