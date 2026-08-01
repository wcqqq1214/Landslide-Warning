"""Behavioral tests for the v3 non-formal Ootang spatial fusion rules."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.levels import WarningLevel
from warning.operational_v2_fusion import fuse_station_evidence_families
from warning.operational_v3_fusion import fuse_site_spatial_blocks_v3

BLOCKS = {
    "O1": ("MJ9", "MJ1", "MJ3"),
    "O2": ("ATU4", "ATU5", "ATU3"),
    "O3": ("ATU2", "ATU1"),
}


def _station(level: WarningLevel = WarningLevel.GREEN):
    return fuse_station_evidence_families(
        interval_level=level,
        velocity_level=WarningLevel.GREEN,
        tangent_angle_level=WarningLevel.GREEN,
        delta_v_state="near_zero",
    )


def _fuse(results):
    return fuse_site_spatial_blocks_v3(
        results,
        blocks=BLOCKS,
        minimum_assessable_station_count=3,
        require_all_blocks_for_any_site_level=True,
        minimum_supporting_stations=2,
        minimum_supporting_blocks=2,
    )


class OperationalV3SpatialSiteFusionTests(unittest.TestCase):
    def test_any_site_colour_requires_global_assessable_coverage(self):
        result = _fuse(
            {
                "MJ9": _station(WarningLevel.YELLOW),
                "ATU4": _station(WarningLevel.YELLOW),
            }
        )

        self.assertEqual(result.status, "insufficient_assessable_coverage")
        self.assertIsNone(result.site_confirmed_level)
        self.assertEqual(result.local_max_candidate_level, WarningLevel.YELLOW)
        self.assertFalse(result.coverage_complete)

    def test_global_coverage_requires_every_spatial_block(self):
        result = _fuse(
            {
                "MJ9": _station(WarningLevel.YELLOW),
                "MJ1": _station(),
                "ATU4": _station(WarningLevel.YELLOW),
            }
        )

        self.assertEqual(result.status, "insufficient_assessable_coverage")
        self.assertIsNone(result.site_confirmed_level)
        self.assertEqual(result.assessable_blocks, ("O1", "O2"))

    def test_complete_all_green_coverage_confirms_site_green(self):
        result = _fuse(
            {
                "MJ9": _station(),
                "ATU4": _station(),
                "ATU2": _station(),
            }
        )

        self.assertEqual(result.status, "valid")
        self.assertEqual(result.site_confirmed_level, WarningLevel.GREEN)
        self.assertEqual(result.local_max_candidate_level, WarningLevel.GREEN)
        self.assertEqual(result.local_attention_status, "none")

    def test_isolated_blue_is_local_attention_while_site_remains_green(self):
        result = _fuse(
            {
                "MJ9": _station(WarningLevel.BLUE),
                "ATU4": _station(),
                "ATU2": _station(),
            }
        )

        self.assertEqual(result.status, "valid")
        self.assertEqual(result.site_confirmed_level, WarningLevel.GREEN)
        self.assertEqual(result.local_max_candidate_level, WarningLevel.BLUE)
        self.assertEqual(result.local_attention_status, "localized_blue_attention")
        self.assertEqual(result.local_max_candidate_stations, ("MJ9",))

    def test_cross_block_blue_candidates_confirm_site_blue(self):
        result = _fuse(
            {
                "MJ9": _station(WarningLevel.BLUE),
                "ATU4": _station(WarningLevel.BLUE),
                "ATU2": _station(),
            }
        )

        self.assertEqual(result.status, "valid")
        self.assertEqual(result.site_confirmed_level, WarningLevel.BLUE)
        self.assertEqual(result.local_max_candidate_level, WarningLevel.BLUE)
        self.assertEqual(result.local_attention_status, "none")
        self.assertEqual(result.contributing_stations, ("ATU4", "MJ9"))
        self.assertEqual(result.contributing_blocks, ("O1", "O2"))

    def test_same_block_blue_candidates_are_local_attention_not_site_blue(self):
        result = _fuse(
            {
                "MJ9": _station(WarningLevel.BLUE),
                "MJ1": _station(WarningLevel.BLUE),
                "ATU4": _station(),
                "ATU2": _station(),
            }
        )

        self.assertEqual(result.status, "valid")
        self.assertEqual(result.site_confirmed_level, WarningLevel.GREEN)
        self.assertEqual(result.local_max_candidate_level, WarningLevel.BLUE)
        self.assertEqual(result.local_attention_status, "localized_blue_attention")

    def test_unconfirmed_yellow_candidate_is_not_downgraded_to_blue(self):
        result = _fuse(
            {
                "MJ9": _station(WarningLevel.YELLOW),
                "ATU4": _station(WarningLevel.BLUE),
                "ATU2": _station(WarningLevel.BLUE),
            }
        )

        self.assertEqual(result.status, "candidate_not_site_confirmed")
        self.assertIsNone(result.site_confirmed_level)
        self.assertEqual(result.local_max_candidate_level, WarningLevel.YELLOW)
        self.assertEqual(result.local_attention_status, "none")

    def test_site_confirmation_and_local_max_keep_separate_levels(self):
        result = _fuse(
            {
                "MJ9": _station(WarningLevel.RED),
                "ATU4": _station(WarningLevel.YELLOW),
                "ATU2": _station(),
            }
        )

        self.assertEqual(result.status, "valid")
        self.assertEqual(result.site_confirmed_level, WarningLevel.YELLOW)
        self.assertEqual(result.local_max_candidate_level, WarningLevel.RED)
        self.assertEqual(result.contributing_stations, ("ATU4", "MJ9"))
        self.assertEqual(result.contributing_blocks, ("O1", "O2"))
        self.assertEqual(result.local_max_candidate_stations, ("MJ9",))

    def test_highest_cross_block_supported_level_is_selected(self):
        cases = (
            (WarningLevel.ORANGE, WarningLevel.ORANGE),
            (WarningLevel.RED, WarningLevel.RED),
        )
        for second_level, expected in cases:
            with self.subTest(second_level=second_level):
                result = _fuse(
                    {
                        "MJ9": _station(WarningLevel.RED),
                        "ATU4": _station(second_level),
                        "ATU2": _station(),
                    }
                )

                self.assertEqual(result.status, "valid")
                self.assertEqual(result.site_confirmed_level, expected)
                self.assertEqual(result.local_max_candidate_level, WarningLevel.RED)

    def test_same_block_orange_candidates_can_only_confirm_cross_block_yellow(self):
        result = _fuse(
            {
                "MJ9": _station(WarningLevel.ORANGE),
                "MJ1": _station(WarningLevel.ORANGE),
                "ATU4": _station(WarningLevel.YELLOW),
                "ATU2": _station(),
            }
        )

        self.assertEqual(result.status, "valid")
        self.assertEqual(result.site_confirmed_level, WarningLevel.YELLOW)
        self.assertEqual(result.local_max_candidate_level, WarningLevel.ORANGE)
        self.assertEqual(result.contributing_stations, ("ATU4", "MJ1", "MJ9"))
        self.assertEqual(result.contributing_blocks, ("O1", "O2"))

    def test_record_exposes_explicit_and_compatibility_axes(self):
        result = _fuse(
            {
                "MJ9": _station(WarningLevel.RED),
                "ATU4": _station(WarningLevel.YELLOW),
                "ATU2": _station(),
            }
        )

        record = result.to_record(
            minimum_assessable_station_count=3,
            require_all_blocks_for_any_site_level=True,
            minimum_supporting_stations=2,
            minimum_supporting_blocks=2,
        )

        self.assertEqual(record["site_level"], int(WarningLevel.YELLOW))
        self.assertEqual(record["site_confirmed_color"], "yellow")
        self.assertEqual(record["site_candidate_level"], int(WarningLevel.RED))
        self.assertEqual(record["local_max_candidate_color"], "red")
        self.assertEqual(record["local_attention_status"], "none")


if __name__ == "__main__":
    unittest.main()
