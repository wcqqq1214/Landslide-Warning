"""Keep documented draft policy metadata aligned with its isolated modules."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.protocol import (  # noqa: E402
    ProtocolNotFrozenError,
    load_protocol,
    protocol_content_sha256,
    require_frozen_protocol,
    unresolved_item_ids,
)
from warning.interval_state import (  # noqa: E402
    CALIBRATION_CHECK_NAMES,
    NORMAL_90_QUANTILE,
)
from warning.delta_v_diagnostics import (  # noqa: E402
    DIAGNOSTIC_STATUS as DELTA_V_DIAGNOSTIC_STATUS,
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
        delta_v_candidate = protocol["confirmed"]["delta_v"]["diagnostic_candidate"]
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
        self.assertEqual(delta_v_candidate["status"], DELTA_V_DIAGNOSTIC_STATUS)
        self.assertEqual(delta_v_candidate["near_zero_tolerance"], "unconfigured")
        self.assertEqual(
            delta_v_candidate["calibration_kinematics_scope"],
            "exact_station_calibration_prediction_dates",
        )
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

    def test_protocol_content_fingerprint_changes_when_policy_content_changes(self):
        protocol = load_protocol()
        identical_copy = copy.deepcopy(protocol)
        revised = copy.deepcopy(protocol)
        revised["unresolved_items"].append(
            {
                "id": "example_additional_gate",
                "reason": "test-only protocol-content change",
                "required_before_formal_run": True,
            }
        )

        fingerprint = protocol_content_sha256(protocol)

        self.assertEqual(fingerprint, protocol_content_sha256(identical_copy))
        self.assertNotEqual(fingerprint, protocol_content_sha256(revised))
        self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")

    def test_protocol_content_fingerprint_ignores_json_key_order_and_whitespace(self):
        def reorder_keys(value):
            if isinstance(value, dict):
                return {
                    key: reorder_keys(value[key])
                    for key in reversed(tuple(value.keys()))
                }
            if isinstance(value, list):
                return [reorder_keys(item) for item in value]
            return value

        protocol = load_protocol()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compact_path = root / "compact.json"
            rearranged_path = root / "rearranged.json"
            compact_path.write_text(
                json.dumps(protocol, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            rearranged_path.write_text(
                json.dumps(reorder_keys(protocol), ensure_ascii=False, indent=4),
                encoding="utf-8",
            )

            compact = load_protocol(compact_path)
            rearranged = load_protocol(rearranged_path)

        self.assertEqual(
            protocol_content_sha256(compact),
            protocol_content_sha256(rearranged),
        )


if __name__ == "__main__":
    unittest.main()
