import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "code"))

from warning import warning_fusion  # noqa: E402

RULE_TEST_STATIONS = ("MJ9", "MJ1", "MJ3")


class WarningFusionTests(unittest.TestCase):
    def _fuse_rule_fixture(self, v0_levels, alpha):
        return warning_fusion.fuse_warning_levels(
            v0_levels,
            alpha,
            key_stations=RULE_TEST_STATIONS,
        )

    def test_default_key_stations_include_all_warning_stations(self):
        self.assertEqual(
            warning_fusion.KEY_STATIONS,
            tuple(warning_fusion.WARNING_STATIONS),
        )
        self.assertEqual(len(warning_fusion.KEY_STATIONS), 8)

    def test_v0_level_is_never_downgraded(self):
        alpha = pd.DataFrame({
            "MJ9_alpha_level": [0, 0, 0, 0],
            "MJ1_alpha_level": [0, 0, 0, 0],
            "MJ3_alpha_level": [0, 0, 0, 0],
        })

        result = self._fuse_rule_fixture([0, 1, 2, 3], alpha)

        self.assertEqual(result["final_level"].tolist(), [0, 1, 2, 3])

    def test_single_alpha_station_is_capped_at_yellow_when_v0_is_green(self):
        alpha = pd.DataFrame({
            "MJ9_alpha_level": [3],
            "MJ1_alpha_level": [0],
            "MJ3_alpha_level": [0],
        })

        result = self._fuse_rule_fixture([0], alpha)

        self.assertEqual(result.loc[0, "final_level"], 1)
        self.assertEqual(result.loc[0, "fusion_reason"], "alpha_watch")

    def test_multiple_alpha_stations_can_confirm_higher_level(self):
        alpha = pd.DataFrame({
            "MJ9_alpha_level": [2],
            "MJ1_alpha_level": [1],
            "MJ3_alpha_level": [0],
        })

        result = self._fuse_rule_fixture([0], alpha)

        self.assertEqual(result.loc[0, "final_level"], 2)
        self.assertEqual(
            result.loc[0, "fusion_reason"],
            "multi_station_confirmed",
        )

    def test_alpha_can_upgrade_non_green_v0_as_multiscale_confirmation(self):
        alpha = pd.DataFrame({
            "MJ9_alpha_level": [3],
            "MJ1_alpha_level": [0],
            "MJ3_alpha_level": [0],
        })

        result = self._fuse_rule_fixture([1], alpha)

        self.assertEqual(result.loc[0, "final_level"], 3)
        self.assertEqual(
            result.loc[0, "fusion_reason"],
            "multi_scale_confirmed",
        )

    def test_output_is_grouped_under_warning_fusion_figures(self):
        self.assertEqual(
            warning_fusion.OUT_CSV.parent,
            ROOT / "figures" / "warning_fusion",
        )


if __name__ == "__main__":
    unittest.main()
