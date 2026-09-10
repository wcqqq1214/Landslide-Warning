from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from physics_guided.training import setup
from physics_guided_forecast_error.artifacts import ROOT, read_json
from physics_guided_pinn.run_substep_audit import load_npz
from physics_guided_state_pinn import verify as frozen
from physics_guided_state_pinn.postcheck import SavedReplayAudit, saved_replay_contract
from physics_guided_state_pinn.workflow import load_bundle, replay_audit, specification


class SavedReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup(0)
        cls.spec = specification()
        cls.bundle = load_bundle(cls.spec, 342)
        directory = ROOT / "results/ootang_bplus_v1_9/20260911_state_pinn/model_342_0"
        cls.prediction = load_npz(directory / "P.npz")
        cls.recorded = load_npz(directory / "R.npz")
        cls.stored = read_json(directory / "replay_audit.json")

    def test_saved_background_adds_one_check_and_legacy_report_still_matches_exactly(
        self,
    ):
        _, raw = replay_audit(self.bundle, self.prediction, self.recorded)
        self.assertTrue(raw["passed"])
        self.assertNotEqual(raw, self.stored)  # The real original failure.
        self.assertEqual(
            set(raw["checks"]) - set(self.stored["checks"]),
            {"daily_recorded_background"},
        )
        adapter = SavedReplayAudit(self.spec)
        _, corrected = adapter(self.bundle, self.prediction, self.recorded)
        self.assertEqual(corrected, self.stored)
        extra = adapter.reconciliations[0]["retained_additional_check"][
            "daily_recorded_background"
        ]
        self.assertTrue(extra["passed"])
        self.assertEqual(extra["max_absolute_error"], 0)
        self.assertIn("background", self.recorded)

    def test_corrupted_saved_background_is_not_discarded(self):
        changed = dict(self.recorded)
        changed["background"] = changed["background"].copy()
        changed["background"][10, 0] += 0.01
        with self.assertRaisesRegex(ArithmeticError, "mechanical checks"):
            SavedReplayAudit(self.spec)(self.bundle, self.prediction, changed)

    def test_physical_state_corruption_is_not_masked_by_schema_reconciliation(self):
        changed = dict(self.recorded)
        changed["current"] = changed["current"].copy()
        changed["current"][3, 0] += 1
        with self.assertRaisesRegex(ArithmeticError, "mechanical checks"):
            SavedReplayAudit(self.spec)(self.bundle, self.prediction, changed)

    def test_unknown_fields_and_wrong_background_shape_fail(self):
        for changed in (
            {**self.recorded, "unknown": 1},
            {**self.recorded, "background": self.recorded["background"][:1]},
        ):
            with self.assertRaises(ValueError):
                SavedReplayAudit(self.spec)(self.bundle, self.prediction, changed)

    def test_original_verifier_binding_restored_after_failure(self):
        original = frozen.replay_audit
        with self.assertRaisesRegex(RuntimeError, "deliberate failure"):
            with saved_replay_contract(SavedReplayAudit(self.spec)):
                raise RuntimeError("deliberate failure")
        self.assertIs(frozen.replay_audit, original)


if __name__ == "__main__":
    unittest.main()
