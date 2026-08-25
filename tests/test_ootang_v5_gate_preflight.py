"""Fail-closed contracts for the Ootang v5 G0--G4 gate register."""

from __future__ import annotations

from contextlib import contextmanager
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from typing import Iterator


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning import ootang_v5_gate_preflight as preflight  # noqa: E402


REGISTER_PATH = ROOT / "config" / "ootang_v5_gate_register.v1.json"


def _load_register_payload() -> dict[str, object]:
    return json.loads(REGISTER_PATH.read_text(encoding="utf-8"))


@contextmanager
def _temporary_register(
    payload: dict[str, object],
    *,
    raw_text: str | None = None,
    filename: str = "gate_register.json",
) -> Iterator[Path]:
    """Write a test register inside the repo so evidence stays repo-scoped."""

    with tempfile.TemporaryDirectory(dir=ROOT) as directory:
        path = Path(directory) / filename
        text = raw_text if raw_text is not None else json.dumps(payload, indent=2)
        path.write_text(text, encoding="utf-8")
        yield path


class CurrentGateRegisterTests(unittest.TestCase):
    def test_current_register_is_g0_pass_g1_through_g4_blocked(self):
        evaluation = preflight.evaluate_g0_g4()

        self.assertEqual(
            evaluation.effective_gates,
            {
                "G0": preflight.PASS,
                "G1": preflight.BLOCKED,
                "G2": preflight.BLOCKED,
                "G3": preflight.BLOCKED,
                "G4": preflight.BLOCKED,
            },
        )
        self.assertFalse(evaluation.g0_g4_preflight_passed)
        for gate in ("G1", "G2", "G3", "G4"):
            self.assertTrue(evaluation.blocker_codes[gate])
        self.assertEqual(evaluation.blocker_codes["G0"], ())

        report = evaluation.as_dict()
        self.assertEqual(report["blocked_gates"], ["G1", "G2", "G3", "G4"])
        self.assertFalse(report["ngboost_inference_output"])
        self.assertFalse(report["v5_fusion_output"])
        self.assertFalse(report["formal_warning_output"])
        self.assertFalse(report["vajont_used"])
        self.assertFalse(report["g0_g4_preflight_passed"])
        self.assertFalse(report["g5a_authorization_evaluated"])
        self.assertFalse(report["g5a_authorized"])

    def test_require_g0_g4_raises_with_all_blocked_gate_codes(self):
        evaluation = preflight.evaluate_g0_g4()

        with self.assertRaisesRegex(
            preflight.G0G4PreflightBlockedError,
            r"G0--G4 preflight is blocked: G1=.*; G2=.*; G3=.*; G4=",
        ):
            preflight.require_g0_g4_preflight()

        self.assertFalse(evaluation.g0_g4_preflight_passed)

    def test_require_g0_g4_accepts_no_alternate_register_or_root(self):
        with self.assertRaises(TypeError):
            preflight.require_g0_g4_preflight(REGISTER_PATH)
        with self.assertRaises(TypeError):
            preflight.require_g0_g4_preflight(root=ROOT)

    def test_cli_report_is_deterministic_and_require_exits_two(self):
        command = [sys.executable, str(preflight.__file__)]
        first = subprocess.run(
            [*command, "--report"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        second = subprocess.run(
            [*command, "--report"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        required = subprocess.run(
            [*command, "--require-g0-g4"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        default = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        alternate = subprocess.run(
            [*command, "--register", str(REGISTER_PATH), "--report"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(first.stderr, "")
        report = json.loads(first.stdout)
        self.assertEqual(report["effective_gates"]["G0"], preflight.PASS)
        self.assertEqual(report["blocked_gates"], ["G1", "G2", "G3", "G4"])
        self.assertFalse(report["g0_g4_preflight_passed"])
        self.assertFalse(report["g5a_authorization_evaluated"])
        self.assertFalse(report["g5a_authorized"])

        for result in (required, default):
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertIn("G0--G4 preflight is blocked", result.stderr)
        self.assertEqual(alternate.returncode, 2)
        self.assertEqual(alternate.stdout, "")
        self.assertIn("unrecognized arguments: --register", alternate.stderr)


class GateRegisterRejectionTests(unittest.TestCase):
    def setUp(self):
        self.payload = _load_register_payload()

    def _assert_rejected(
        self,
        payload: dict[str, object],
        message: str | None = None,
    ) -> None:
        with _temporary_register(payload) as path:
            context = (
                self.assertRaisesRegex(preflight.GateRegisterValidationError, message)
                if message is not None
                else self.assertRaises(preflight.GateRegisterValidationError)
            )
            with context:
                preflight.load_and_validate_register(path)

    def test_rejects_schema_drift(self):
        changed = copy.deepcopy(self.payload)
        changed["unexpected_field"] = True

        self._assert_rejected(changed, r"register keys drifted")

    def test_rejects_integer_alias_for_frozen_top_boolean(self):
        changed = copy.deepcopy(self.payload)
        changed["formal_warning_output"] = 0

        self._assert_rejected(changed, r"register.formal_warning_output")

    def test_rejects_duplicate_json_key(self):
        raw_text = json.dumps(self.payload, indent=2).replace(
            '"schema_version":',
            '"schema_version": "duplicate",\n  "schema_version":',
            1,
        )

        with _temporary_register(self.payload, raw_text=raw_text) as path:
            with self.assertRaisesRegex(
                preflight.GateRegisterValidationError,
                r"duplicate key: schema_version",
            ):
                preflight.load_and_validate_register(path)

    def test_rejects_frozen_top_document_and_implementation_path_aliases(self):
        aliases = (
            ("policy_document", "inventory_document"),
            ("inventory_document", "policy_document"),
        )
        for target, source in aliases:
            with self.subTest(target=target, source=source):
                changed = copy.deepcopy(self.payload)
                changed[target] = copy.deepcopy(changed[source])
                self._assert_rejected(changed)

        changed = copy.deepcopy(self.payload)
        changed["implementation_sources"]["preflight"] = copy.deepcopy(
            changed["policy_document"]
        )
        self._assert_rejected(changed)

    def test_rejects_frozen_blocker_decision_and_evidence_role_drift(self):
        changed = copy.deepcopy(self.payload)
        changed["gates"]["G1"]["blocker_codes"].append("invented_blocker")
        self._assert_rejected(changed)

        changed = copy.deepcopy(self.payload)
        changed["gates"]["G1"]["decision"]["label_definition"] = "invented"
        self._assert_rejected(changed)

        changed = copy.deepcopy(self.payload)
        changed["gates"]["G1"]["evidence"][0]["role"] = "invented_role"
        self._assert_rejected(changed)

    def test_rejects_one_evidence_path_masquerading_as_two_roles(self):
        changed = copy.deepcopy(self.payload)
        first = changed["gates"]["G1"]["evidence"][0]
        second = changed["gates"]["G1"]["evidence"][1]
        second["path"] = first["path"]
        second["sha256"] = first["sha256"]

        self._assert_rejected(changed)

    def test_rejects_evidence_hash_drift(self):
        changed = copy.deepcopy(self.payload)
        changed["gates"]["G0"]["evidence"][0]["sha256"] = "0" * 64

        self._assert_rejected(changed, r"G0 evidence SHA-256 does not match")

    def test_rejects_evidence_path_traversal_without_reading_target(self):
        changed = copy.deepcopy(self.payload)
        changed["gates"]["G0"]["evidence"][0]["path"] = "../outside.json"

        self._assert_rejected(changed)

    def test_rejects_vajont_and_review_evidence_paths_without_reading_them(self):
        paths = (
            "data/vajont_forbidden.xlsx",
            "review.md",
            "docs/review.md",
        )
        for forbidden_path in paths:
            with self.subTest(path=forbidden_path):
                changed = copy.deepcopy(self.payload)
                changed["gates"]["G0"]["evidence"][0]["path"] = forbidden_path
                self._assert_rejected(changed)

    def test_rejects_symlink_evidence_resolving_to_vajont_without_reading_it(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            temporary_root = Path(directory)
            link = temporary_root / "apparently_safe_evidence.json"
            forbidden_target = (
                ROOT
                / "data"
                / "vajont_gate_test_target_never_create_8e397a65.json"
            )
            link.symlink_to(forbidden_target)

            with self.assertRaisesRegex(
                preflight.GateRegisterValidationError,
                r"outside scope",
            ):
                preflight._resolve_repo_file(
                    link.relative_to(ROOT).as_posix(),
                    root=ROOT,
                    name="G0.evidence[0]",
                )

    def test_rejects_g1_self_generated_label_policy_drift(self):
        changed = copy.deepcopy(self.payload)
        changed["gates"]["G1"]["decision"][
            "self_generated_labels_allowed"
        ] = True

        self._assert_rejected(changed, r"cannot allow self-generated labels")

    def test_rejects_g1_pass_without_independent_approval(self):
        changed = copy.deepcopy(self.payload)
        changed["gates"]["G1"]["declared_status"] = preflight.PASS
        changed["gates"]["G1"]["blocker_codes"] = []

        self._assert_rejected(changed, r"G1 v1 cannot promote to PASS")

    def test_rejects_g2_existing_model_fit_role_drift(self):
        changed = copy.deepcopy(self.payload)
        changed["gates"]["G2"]["decision"]["existing_splits"][
            "fit"
        ]["role"] = "automatic_v0_selection"

        self._assert_rejected(changed, r"G2 existing split roles drifted")

    def test_rejects_g2_v0_selection_window_drift(self):
        expected = {
            "start_date": "2016-07-01",
            "end_date": "2019-02-02",
            "station_rows": 947,
            "role": "automatic_v0_fit_end_bounded_kinematics_only",
        }
        self.assertEqual(
            self.payload["gates"]["G2"]["decision"]["v0_selection_window"],
            expected,
        )
        mutations = {
            "start_date": "2016-08-06",
            "end_date": "2019-02-01",
            "station_rows": 946,
            "role": "model_fit_training_and_feature_statistics_only",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                changed = copy.deepcopy(self.payload)
                changed["gates"]["G2"]["decision"]["v0_selection_window"][
                    field
                ] = value
                self._assert_rejected(
                    changed,
                    r"G2 V0 selection window drifted",
                )

    def test_rejects_g3_availability_policy_and_coverage_drift(self):
        mutations = (
            (
                ("hard_policy", "unavailable_as_negative_or_green"),
                True,
                r"G3 hard availability policy drifted",
            ),
            (
                ("current_snapshot", "station_day_coverage_fraction"),
                1.0,
                r"G3 current coverage snapshot drifted",
            ),
        )
        for path, value, message in mutations:
            with self.subTest(path=path):
                changed = copy.deepcopy(self.payload)
                changed["gates"]["G3"]["decision"][path[0]][path[1]] = value
                self._assert_rejected(changed, message)

    def test_rejects_integer_alias_for_g3_false_policy_value(self):
        changed = copy.deepcopy(self.payload)
        changed["gates"]["G3"]["decision"]["hard_policy"][
            "unavailable_as_negative_or_green"
        ] = 0

        self._assert_rejected(changed, r"G3 hard availability policy drifted")

    def test_rejects_g4_metric_and_unapproved_threshold_drift(self):
        mutations = (
            (
                lambda changed: changed["gates"]["G4"]["decision"].update(
                    {"primary_metrics": ["accuracy"]}
                ),
                r"G4 primary metrics drifted",
            ),
            (
                lambda changed: changed["gates"]["G4"]["decision"][
                    "thresholds"
                ].update({"event_recall_min": 0.8}),
                r"must remain null until approval",
            ),
        )
        for mutate, message in mutations:
            with self.subTest(message=message):
                changed = copy.deepcopy(self.payload)
                mutate(changed)
                self._assert_rejected(changed, message)

    def test_rejects_nan_before_gate_validation(self):
        raw_text = json.dumps(self.payload).replace(
            '"joint_one_sided_confidence": 0.975',
            '"joint_one_sided_confidence": NaN',
            1,
        )
        self.assertIn("NaN", raw_text)

        with _temporary_register(self.payload, raw_text=raw_text) as path:
            with self.assertRaisesRegex(
                preflight.GateRegisterValidationError,
                r"forbidden JSON constant: NaN",
            ):
                preflight.load_and_validate_register(path)

    def test_canonical_hash_is_stable_across_key_order_and_whitespace(self):
        reversed_payload = {
            key: self.payload[key] for key in reversed(list(self.payload))
        }
        with _temporary_register(
            self.payload,
            raw_text=json.dumps(self.payload, indent=2),
            filename="pretty.json",
        ) as first_path:
            first = preflight.evaluate_g0_g4(first_path)
        with _temporary_register(
            reversed_payload,
            raw_text=json.dumps(reversed_payload, separators=(",", ":")),
            filename="compact.json",
        ) as second_path:
            second = preflight.evaluate_g0_g4(second_path)

        self.assertEqual(
            first.register_content_sha256,
            second.register_content_sha256,
        )
        self.assertNotEqual(first.register_file_sha256, second.register_file_sha256)


if __name__ == "__main__":
    unittest.main()
