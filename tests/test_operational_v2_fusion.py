"""Behavioral tests for the v2 non-formal Ootang spatial fusion rules."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.levels import WarningLevel
from warning.operational_v2_fusion import (
    fuse_site_spatial_blocks,
    fuse_station_evidence_families,
)

BLOCKS = {
    "O1": ("MJ9", "MJ1", "MJ3"),
    "O2": ("ATU4", "ATU5", "ATU3"),
    "O3": ("ATU2", "ATU1"),
}


def _station(
    *,
    interval: WarningLevel = WarningLevel.GREEN,
    velocity: WarningLevel = WarningLevel.GREEN,
    tangent: WarningLevel = WarningLevel.GREEN,
    delta_v: str = "near_zero",
):
    return fuse_station_evidence_families(
        interval_level=interval,
        velocity_level=velocity,
        tangent_angle_level=tangent,
        delta_v_state=delta_v,
    )


class OperationalV2StationFusionTests(unittest.TestCase):
    def test_velocity_and_tangent_form_one_kinematic_family(self):
        result = _station(
            interval=WarningLevel.RED,
            velocity=WarningLevel.BLUE,
            tangent=WarningLevel.YELLOW,
        )

        self.assertEqual(result.status, "valid")
        self.assertEqual(result.candidate_level, WarningLevel.RED)
        self.assertEqual(result.kinematic_level, WarningLevel.YELLOW)
        self.assertEqual(result.evidence_families, ("interval", "kinematic"))
        self.assertEqual(result.evidence_family_count, 2)
        self.assertEqual(
            result.confirmation_status,
            "interval_and_kinematic_elevated",
        )

    def test_positive_delta_v_marks_acceleration_without_changing_candidate_color(self):
        result = _station(
            interval=WarningLevel.RED,
            delta_v="positive",
        )

        self.assertEqual(result.candidate_level, WarningLevel.RED)
        self.assertEqual(result.acceleration_status, "accelerating")
        self.assertEqual(
            result.confirmation_status,
            "interval_only_accelerating",
        )

    def test_single_family_anomaly_remains_a_visible_assessable_candidate(self):
        result = _station(interval=WarningLevel.RED)

        self.assertEqual(result.status, "valid")
        self.assertEqual(result.candidate_level, WarningLevel.RED)
        self.assertEqual(result.confirmation_status, "interval_only_not_accelerating")

    def test_nonvalid_input_still_blocks_station_assessment(self):
        result = fuse_station_evidence_families(
            interval_level=WarningLevel.GREEN,
            velocity_level=None,
            tangent_angle_level=WarningLevel.GREEN,
            delta_v_state="near_zero",
            input_statuses={"velocity": "invalid"},
        )

        self.assertEqual(result.status, "invalid")
        self.assertIsNone(result.candidate_level)


class OperationalV2SpatialSiteFusionTests(unittest.TestCase):
    def test_non_green_site_result_requires_minimum_assessable_station_coverage(self):
        results = {
            "MJ9": _station(interval=WarningLevel.YELLOW),
            "ATU4": _station(interval=WarningLevel.YELLOW),
        }

        result = fuse_site_spatial_blocks(
            results,
            blocks=BLOCKS,
            minimum_assessable_station_count=3,
            require_all_blocks_for_green=True,
            minimum_supporting_stations=2,
            minimum_supporting_blocks=2,
        )

        self.assertEqual(result.status, "insufficient_assessable_coverage")
        self.assertIsNone(result.level)
        self.assertEqual(result.candidate_level, WarningLevel.YELLOW)
        self.assertEqual(result.assessable_station_count, 2)

    def test_v2_keeps_all_block_coverage_as_a_green_only_requirement(self):
        results = {
            "MJ9": _station(interval=WarningLevel.YELLOW),
            "MJ1": _station(),
            "ATU4": _station(interval=WarningLevel.YELLOW),
        }

        result = fuse_site_spatial_blocks(
            results,
            blocks=BLOCKS,
            minimum_assessable_station_count=3,
            require_all_blocks_for_green=True,
            minimum_supporting_stations=2,
            minimum_supporting_blocks=2,
        )

        self.assertEqual(result.status, "valid")
        self.assertEqual(result.level, WarningLevel.YELLOW)
        self.assertFalse(result.coverage_complete)

    def test_same_block_red_candidates_do_not_confirm_a_whole_body_red_alert(self):
        results = {
            "MJ9": _station(interval=WarningLevel.RED, delta_v="positive"),
            "MJ1": _station(interval=WarningLevel.RED, delta_v="positive"),
            "MJ3": _station(interval=WarningLevel.RED, delta_v="positive"),
            "ATU4": _station(),
            "ATU5": _station(),
            "ATU3": _station(),
            "ATU2": _station(),
            "ATU1": _station(),
        }

        result = fuse_site_spatial_blocks(
            results,
            blocks=BLOCKS,
            minimum_assessable_station_count=3,
            require_all_blocks_for_green=True,
            minimum_supporting_stations=2,
            minimum_supporting_blocks=2,
        )

        self.assertEqual(result.status, "candidate_not_site_confirmed")
        self.assertIsNone(result.level)
        self.assertEqual(result.candidate_level, WarningLevel.RED)
        self.assertEqual(result.candidate_blocks, ("O1",))

    def test_cross_block_red_candidates_confirm_site_red(self):
        results = {
            "MJ9": _station(),
            "MJ1": _station(),
            "MJ3": _station(),
            "ATU4": _station(interval=WarningLevel.RED, delta_v="positive"),
            "ATU5": _station(),
            "ATU3": _station(),
            "ATU2": _station(),
            "ATU1": _station(interval=WarningLevel.RED, delta_v="positive"),
        }

        result = fuse_site_spatial_blocks(
            results,
            blocks=BLOCKS,
            minimum_assessable_station_count=3,
            require_all_blocks_for_green=True,
            minimum_supporting_stations=2,
            minimum_supporting_blocks=2,
        )

        self.assertEqual(result.status, "valid")
        self.assertEqual(result.level, WarningLevel.RED)
        self.assertEqual(result.contributing_stations, ("ATU1", "ATU4"))
        self.assertEqual(result.contributing_blocks, ("O2", "O3"))

    def test_unconfirmed_red_is_not_downgraded_to_cross_block_blue(self):
        results = {
            "MJ9": _station(interval=WarningLevel.RED),
            "MJ1": _station(),
            "MJ3": _station(),
            "ATU4": _station(interval=WarningLevel.BLUE),
            "ATU5": _station(),
            "ATU3": _station(),
            "ATU2": _station(interval=WarningLevel.BLUE),
            "ATU1": _station(),
        }

        result = fuse_site_spatial_blocks(
            results,
            blocks=BLOCKS,
            minimum_assessable_station_count=3,
            require_all_blocks_for_green=True,
            minimum_supporting_stations=2,
            minimum_supporting_blocks=2,
            cross_block_confirmation_minimum_level=WarningLevel.YELLOW,
        )

        self.assertEqual(result.status, "candidate_not_site_confirmed")
        self.assertIsNone(result.level)
        self.assertEqual(result.candidate_level, WarningLevel.RED)
        self.assertEqual(result.reason, "no_2_station_2_block_yellow_or_higher_support")

    def test_blue_is_visible_when_it_is_the_highest_candidate(self):
        results = {
            "MJ9": _station(interval=WarningLevel.BLUE),
            "ATU4": _station(),
            "ATU2": _station(),
        }

        result = fuse_site_spatial_blocks(
            results,
            blocks=BLOCKS,
            minimum_assessable_station_count=3,
            require_all_blocks_for_green=True,
            minimum_supporting_stations=2,
            minimum_supporting_blocks=2,
            cross_block_confirmation_minimum_level=WarningLevel.YELLOW,
        )

        self.assertEqual(result.status, "valid")
        self.assertEqual(result.level, WarningLevel.BLUE)
        self.assertEqual(result.candidate_level, WarningLevel.BLUE)
        self.assertEqual(
            result.reason,
            "highest_candidate_blue_no_cross_block_confirmation_required",
        )

    def test_whole_body_green_requires_assessable_coverage_of_every_block(self):
        fully_covered = {
            "MJ9": _station(),
            "ATU4": _station(),
            "ATU2": _station(),
        }
        incomplete = {
            "MJ9": _station(),
            "MJ1": _station(),
            "ATU4": _station(),
        }

        complete_result = fuse_site_spatial_blocks(
            fully_covered,
            blocks=BLOCKS,
            minimum_assessable_station_count=3,
            require_all_blocks_for_green=True,
            minimum_supporting_stations=2,
            minimum_supporting_blocks=2,
        )
        incomplete_result = fuse_site_spatial_blocks(
            incomplete,
            blocks=BLOCKS,
            minimum_assessable_station_count=3,
            require_all_blocks_for_green=True,
            minimum_supporting_stations=2,
            minimum_supporting_blocks=2,
        )

        self.assertEqual(complete_result.status, "valid")
        self.assertEqual(complete_result.level, WarningLevel.GREEN)
        self.assertEqual(incomplete_result.status, "insufficient_assessable_coverage")
        self.assertIsNone(incomplete_result.level)
        self.assertEqual(incomplete_result.assessable_blocks, ("O1", "O2"))


if __name__ == "__main__":
    unittest.main()
