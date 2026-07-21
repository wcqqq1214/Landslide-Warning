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
    NORMAL_90_QUANTILE,
    THESIS_FIGURE_5_1_MAPPING_ID,
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
    def test_v0_source_priority_keeps_ootang_thesis_reference_only(self):
        protocol = load_protocol()
        sources = {
            item["source"]: item["role"] for item in protocol["source_evidence"]
        }
        v0_framework = protocol["confirmed"]["v0_framework"]

        ootang_thesis = (
            "literature/韦承谦_基于机器学习方法的水库滑坡位移预测及预警研究——"
            "以藕塘滑坡为例.pdf"
        )
        self.assertEqual(
            sources[ootang_thesis],
            "historical_ootang_v0_reproduction_reference_only_not_a_formal_source",
        )
        self.assertEqual(
            v0_framework["source_priority"]["primary"],
            "specified_word_thesis_chapter_5_equation_5_3",
        )
        self.assertEqual(
            v0_framework["source_priority"]["ootang_graduation_thesis"],
            "reference_only_historical_reproduction_not_a_formal_source",
        )
        self.assertEqual(
            v0_framework["source_priority"]["conflict_resolution"],
            "specified_word_thesis_precedes_ootang_graduation_thesis",
        )

    def test_candidate_metadata_matches_the_reproducible_module_defaults(self):
        protocol = load_protocol()
        interval_candidate = protocol["confirmed"]["interval"]["candidate"]
        v0_candidate = protocol["confirmed"]["v0_framework"]["stable_segment_candidate"]
        delta_v_candidate = protocol["confirmed"]["delta_v"]["diagnostic_candidate"]
        fusion_candidate = protocol["confirmed"]["fusion"]["per_station_candidate"]

        self.assertEqual(v0_candidate["status"], "draft_candidate_not_formal")
        self.assertEqual(
            v0_candidate["candidate_method_id"],
            "raw_velocity_kmeans_initial_low_speed_prefix",
        )
        self.assertEqual(
            v0_candidate["candidate_method_role"],
            "project_specific_comparator_not_specified_word_v0_implementation",
        )
        self.assertEqual(
            v0_candidate["word_thesis_v0_input"],
            "MVIF_trend_displacement_initial_stable_slope",
        )
        self.assertEqual(
            v0_candidate["word_thesis_v0_input_status"],
            "not_implemented_by_this_candidate",
        )
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
        self.assertEqual(
            interval_candidate["status"],
            "source_referenced_mapping_not_formal",
        )
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
            interval_candidate["mapping_basis"],
            THESIS_FIGURE_5_1_MAPPING_ID,
        )
        self.assertEqual(
            interval_candidate["diagnostic_policy"],
            "calibration_diagnostics_are_audit_only_and_do_not_block_mapping",
        )

    def test_candidate_rules_do_not_remove_the_formal_run_gates(self):
        unresolved = unresolved_item_ids(load_protocol())

        self.assertIn("stable_segment_selection", unresolved)
        self.assertIn("tangent_blue_tolerance", unresolved)
        self.assertIn("per_station_fusion_function", unresolved)
        self.assertIn("landslide_body_fusion_function", unresolved)
        self.assertNotIn("interval_calibration_gate", unresolved)
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
