"""Contracts for the non-formal Ootang v4 acceleration draft."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.levels import WarningLevel
from warning.operational_v2_fusion import fuse_station_evidence_families_v4
from warning.operational_run import _load_operational_profile


class OperationalV4FusionTests(unittest.TestCase):
    def test_acceleration_is_an_independent_family_and_can_raise_candidate(self):
        result = fuse_station_evidence_families_v4(
            interval_level=WarningLevel.GREEN,
            velocity_level=WarningLevel.GREEN,
            tangent_angle_level=WarningLevel.GREEN,
            acceleration_level=WarningLevel.YELLOW,
            delta_v_state="positive",
            input_statuses={
                "interval": "valid",
                "velocity": "valid",
                "tangent_angle": "valid",
                "acceleration": "valid",
                "delta_v": "valid",
            },
        )
        self.assertEqual(result.candidate_level, WarningLevel.YELLOW)
        self.assertEqual(result.evidence_families, ("acceleration",))
        self.assertEqual(result.acceleration_level, WarningLevel.YELLOW)
        self.assertIn("acceleration_yellow", result.composite_signal)

    def test_velocity_and_tangent_remain_one_kinematic_family(self):
        result = fuse_station_evidence_families_v4(
            interval_level=WarningLevel.GREEN,
            velocity_level=WarningLevel.YELLOW,
            tangent_angle_level=WarningLevel.BLUE,
            acceleration_level=WarningLevel.GREEN,
            delta_v_state="near_zero",
        )
        self.assertEqual(result.kinematic_level, WarningLevel.YELLOW)
        self.assertEqual(result.evidence_families, ("kinematic_velocity_tangent",))


class OperationalV4ArtifactTests(unittest.TestCase):
    def test_profile_locks_v1_evidence_and_v2_acceleration_extension(self):
        loaded = _load_operational_profile(ROOT / "config" / "ootang_operational_run.v4.draft.json")
        self.assertEqual(loaded.base_protocol["protocol_id"], "ootang-five-level-rule-v1")
        self.assertEqual(loaded.acceleration_protocol["protocol_id"], "ootang-four-indicator-rule-v2")
        self.assertEqual(loaded.profile["acceleration"]["baseline"]["a0_formula"], "max(1.5*A, A+2*sigma_a)")

    def test_materialized_v4_bundle_has_full_grid_and_acceleration_counts(self):
        source = ROOT / "figures" / "warning_operational_draft_v4"
        station = pd.read_csv(source / "ootang_operational_station_timeline.csv")
        site = pd.read_csv(source / "ootang_operational_site_timeline.csv")
        manifest = json.loads((source / "ootang_operational_run_manifest.json").read_text())
        self.assertEqual(len(station), 4112)
        self.assertEqual(len(site), 514)
        self.assertEqual(station["acceleration_level"].value_counts().to_dict(), {0: 4012, 1: 98, 2: 2})
        allowed_families = {"interval", "kinematic_velocity_tangent", "acceleration"}
        family_tokens = {
            token
            for value in station["evidence_families"].dropna().astype(str)
            for token in value.split(";")
            if token
        }
        self.assertTrue(family_tokens)
        self.assertTrue(family_tokens <= allowed_families)
        self.assertNotIn("kinematic", family_tokens)
        self.assertEqual(manifest["acceleration_protocol_extension"]["id"], "ootang-four-indicator-rule-v2")
        self.assertIn("station_fusion_implementation", manifest["implementation_sources"])
        self.assertFalse(station["vajont_used"].any())
        self.assertTrue((station["acceleration_level"] > 0).sum() > 0)
        for figure_manifest in sorted(source.glob("ootang_v4_*_manifest.json")):
            figure = json.loads(figure_manifest.read_text())
            self.assertEqual(set(figure["outputs"]), {"svg", "png"})


if __name__ == "__main__":
    unittest.main()
