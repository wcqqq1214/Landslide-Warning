"""Keep documented draft policy metadata aligned with its isolated modules."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.protocol import (  # noqa: E402
    ProtocolNotFrozenError,
    load_protocol,
    require_frozen_protocol,
    unresolved_item_ids,
)
from warning.interval_state import (  # noqa: E402
    CALIBRATION_CHECK_NAMES,
    NORMAL_90_QUANTILE,
)
from warning.rule_fusion import DEFAULT_MINIMUM_SUPPORT  # noqa: E402
from warning.stable_segment import (  # noqa: E402
    DEFAULT_INIT,
    DEFAULT_N_CLUSTERS,
    DEFAULT_N_INIT,
    DEFAULT_OPENMP_THREAD_LIMIT,
    DEFAULT_RANDOM_STATE,
    DEFAULT_SIGMA_DDOF,
)


class WarningDraftProtocolTests(unittest.TestCase):
    def test_candidate_metadata_matches_the_reproducible_module_defaults(self):
        protocol = load_protocol()
        interval_candidate = protocol["confirmed"]["interval"]["candidate"]
        v0_candidate = protocol["confirmed"]["v0_framework"]["stable_segment_candidate"]
        fusion_candidate = protocol["confirmed"]["fusion"]["per_station_candidate"]

        self.assertEqual(v0_candidate["status"], "draft_candidate_not_formal")
        self.assertEqual(v0_candidate["n_clusters"], DEFAULT_N_CLUSTERS)
        self.assertEqual(v0_candidate["init"], DEFAULT_INIT)
        self.assertEqual(v0_candidate["random_state"], DEFAULT_RANDOM_STATE)
        self.assertEqual(v0_candidate["n_init"], DEFAULT_N_INIT)
        self.assertEqual(
            v0_candidate["openmp_threads"],
            DEFAULT_OPENMP_THREAD_LIMIT,
        )
        self.assertEqual(v0_candidate["sigma"]["ddof"], DEFAULT_SIGMA_DDOF)
        self.assertIn("first_valid_velocity", v0_candidate["audit_fields"])
        self.assertEqual(
            fusion_candidate["minimum_support"],
            DEFAULT_MINIMUM_SUPPORT,
        )
        self.assertEqual(fusion_candidate["status"], "draft_candidate_not_formal")
        self.assertEqual(interval_candidate["status"], "draft_candidate_not_formal")
        self.assertEqual(
            interval_candidate["standard_normal_p90_quantile"],
            NORMAL_90_QUANTILE,
        )
        self.assertEqual(
            tuple(interval_candidate["required_global_checks"]),
            CALIBRATION_CHECK_NAMES,
        )

    def test_candidate_rules_do_not_remove_the_formal_run_gates(self):
        unresolved = unresolved_item_ids(load_protocol())

        self.assertIn("stable_segment_selection", unresolved)
        self.assertIn("tangent_blue_tolerance", unresolved)
        self.assertIn("per_station_fusion_function", unresolved)
        self.assertIn("landslide_body_fusion_function", unresolved)
        with self.assertRaises(ProtocolNotFrozenError):
            require_frozen_protocol()


if __name__ == "__main__":
    unittest.main()
