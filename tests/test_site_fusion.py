"""Behavioral tests for the diagnostic-only landslide-body audit summary."""

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
from warning.site_fusion import (  # noqa: E402
    SITE_FUSION_AUDIT_STATUS,
    summarize_site_fusion_inputs,
)


class SiteFusionAuditTests(unittest.TestCase):
    def test_summary_counts_only_valid_station_levels_without_an_integrated_level(self):
        yellow = fuse_station_indicators(
            interval_level=WarningLevel.YELLOW,
            velocity_level=WarningLevel.YELLOW,
            delta_v_state="near_zero",
            tangent_angle_level=WarningLevel.GREEN,
        )
        red = fuse_station_indicators(
            interval_level=WarningLevel.RED,
            velocity_level=WarningLevel.RED,
            delta_v_state="near_zero",
            tangent_angle_level=WarningLevel.GREEN,
        )
        uncorroborated = fuse_station_indicators(
            interval_level=WarningLevel.RED,
            velocity_level=WarningLevel.GREEN,
            delta_v_state="near_zero",
            tangent_angle_level=WarningLevel.GREEN,
        )
        warmup = fuse_station_indicators(
            interval_level=WarningLevel.GREEN,
            velocity_level=WarningLevel.GREEN,
            delta_v_state=None,
            tangent_angle_level=WarningLevel.GREEN,
            input_statuses={"delta_v": "warmup"},
        )

        audit = summarize_site_fusion_inputs(
            {
                "MJ9": warmup,
                "ATU1": yellow,
                "MJ3": uncorroborated,
                "MJ1": red,
            }
        )

        self.assertEqual(audit.status, SITE_FUSION_AUDIT_STATUS)
        self.assertEqual(audit.total_station_count, 4)
        self.assertEqual(audit.valid_station_count, 2)
        self.assertEqual(audit.elevated_station_count, 2)
        self.assertEqual(audit.max_station_level, WarningLevel.RED)
        self.assertEqual(audit.max_level_stations, ("MJ1",))
        self.assertEqual(audit.uncorroborated_stations, ("MJ3",))
        self.assertEqual(
            dict(audit.level_counts),
            {
                WarningLevel.GREEN: 0,
                WarningLevel.BLUE: 0,
                WarningLevel.YELLOW: 1,
                WarningLevel.ORANGE: 0,
                WarningLevel.RED: 1,
            },
        )
        self.assertEqual(
            dict(audit.station_status_counts),
            {
                "valid": 2,
                "warmup": 1,
                "invalid": 0,
                "not_applicable": 0,
                "uncorroborated": 1,
            },
        )

        record = audit.to_record()
        self.assertFalse(record["formal_warning_output"])
        self.assertIsNone(record["integrated_level"])
        self.assertIsNone(record["integrated_color"])
        self.assertEqual(record["site_fusion_reason"], "site_rule_unconfigured")
        self.assertEqual(record["max_station_color"], "red")
        self.assertEqual(record["max_level_stations"], "MJ1")
        self.assertEqual(record["uncorroborated_stations"], "MJ3")
        self.assertEqual(record["station_count_yellow"], 1)
        self.assertEqual(record["station_count_red"], 1)
        self.assertEqual(record["station_result_status_invalid"], 0)

    def test_summary_has_no_maximum_when_no_station_result_is_valid(self):
        warmup = fuse_station_indicators(
            interval_level=WarningLevel.GREEN,
            velocity_level=WarningLevel.GREEN,
            delta_v_state=None,
            tangent_angle_level=WarningLevel.GREEN,
            input_statuses={"delta_v": "warmup"},
        )
        invalid = fuse_station_indicators(
            interval_level=None,
            velocity_level=WarningLevel.GREEN,
            delta_v_state="negative",
            tangent_angle_level=WarningLevel.GREEN,
            input_statuses={"interval": "invalid"},
        )

        audit = summarize_site_fusion_inputs({"MJ9": warmup, "ATU1": invalid})

        self.assertEqual(audit.valid_station_count, 0)
        self.assertEqual(audit.elevated_station_count, 0)
        self.assertIsNone(audit.max_station_level)
        self.assertEqual(audit.max_level_stations, ())
        self.assertEqual(dict(audit.level_counts)[WarningLevel.RED], 0)
        record = audit.to_record()
        self.assertIsNone(record["max_station_level"])
        self.assertIsNone(record["max_station_color"])

    def test_summary_rejects_empty_or_blank_station_identifier(self):
        green = fuse_station_indicators(
            interval_level=WarningLevel.GREEN,
            velocity_level=WarningLevel.GREEN,
            delta_v_state="negative",
            tangent_angle_level=WarningLevel.GREEN,
        )

        with self.assertRaisesRegex(ValueError, "at least one"):
            summarize_site_fusion_inputs({})
        with self.assertRaisesRegex(ValueError, "station identifiers"):
            summarize_site_fusion_inputs({"   ": green})


if __name__ == "__main__":
    unittest.main()
