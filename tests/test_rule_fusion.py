"""Behavioral tests for the draft per-station multi-indicator fusion rule."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.levels import WarningLevel  # noqa: E402
from warning.rule_fusion import fuse_station_indicators  # noqa: E402


class StationRuleFusionTests(unittest.TestCase):
    def test_two_ordinal_indicators_confirm_the_highest_shared_level(self):
        result = fuse_station_indicators(
            interval_level=WarningLevel.RED,
            velocity_level=WarningLevel.YELLOW,
            delta_v_state="near_zero",
            tangent_angle_level=WarningLevel.GREEN,
        )

        self.assertEqual(result.status, "valid")
        self.assertEqual(result.level, WarningLevel.YELLOW)
        self.assertEqual(result.color, "yellow")
        self.assertEqual(result.contributing_indicators, ("interval", "velocity"))

    def test_positive_delta_v_can_be_the_second_corroborating_indicator(self):
        result = fuse_station_indicators(
            interval_level=WarningLevel.RED,
            velocity_level=WarningLevel.GREEN,
            delta_v_state="positive",
            tangent_angle_level=WarningLevel.GREEN,
        )

        self.assertEqual(result.status, "valid")
        self.assertEqual(result.level, WarningLevel.RED)
        self.assertEqual(result.contributing_indicators, ("interval", "delta_v"))

    def test_a_single_high_signal_is_not_silently_relabelled_as_green(self):
        result = fuse_station_indicators(
            interval_level=WarningLevel.RED,
            velocity_level=WarningLevel.GREEN,
            delta_v_state="near_zero",
            tangent_angle_level=WarningLevel.GREEN,
        )

        self.assertEqual(result.status, "uncorroborated")
        self.assertIsNone(result.level)
        self.assertIsNone(result.color)
        self.assertEqual(result.candidate_max_level, WarningLevel.RED)

    def test_all_green_ordinal_inputs_and_a_nonpositive_trend_are_green(self):
        result = fuse_station_indicators(
            interval_level=WarningLevel.GREEN,
            velocity_level=WarningLevel.GREEN,
            delta_v_state="negative",
            tangent_angle_level=WarningLevel.GREEN,
        )

        self.assertEqual(result.status, "valid")
        self.assertEqual(result.level, WarningLevel.GREEN)
        self.assertEqual(result.color, "green")
        self.assertEqual(result.contributing_indicators, ())

    def test_warmup_and_invalid_inputs_are_explicit_not_silently_dropped(self):
        warmup = fuse_station_indicators(
            interval_level=WarningLevel.GREEN,
            velocity_level=WarningLevel.GREEN,
            delta_v_state=None,
            tangent_angle_level=WarningLevel.GREEN,
            input_statuses={"delta_v": "warmup"},
        )
        invalid = fuse_station_indicators(
            interval_level=WarningLevel.GREEN,
            velocity_level=None,
            delta_v_state="negative",
            tangent_angle_level=WarningLevel.GREEN,
            input_statuses={"velocity": "invalid"},
        )

        self.assertEqual(warmup.status, "warmup")
        self.assertIsNone(warmup.level)
        self.assertEqual(invalid.status, "invalid")
        self.assertIsNone(invalid.level)

    def test_draft_minimum_support_cannot_be_overridden_at_call_time(self):
        with self.assertRaises(TypeError):
            fuse_station_indicators(
                interval_level=WarningLevel.RED,
                velocity_level=WarningLevel.GREEN,
                delta_v_state="positive",
                tangent_angle_level=WarningLevel.GREEN,
                minimum_support=3,
            )


if __name__ == "__main__":
    unittest.main()
