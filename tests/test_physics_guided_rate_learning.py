from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from physics_guided_forecast_error.artifacts import ROOT
from physics_guided_pinn.run_substep_audit import load_npz
from physics_guided_rate_learning.workflow import (
    ARRAY_KEYS,
    POINTS,
    arrays,
    calibrate,
    compare,
    mechanical_audit,
    training_solver,
)
from physics_guided_state_pinn.workflow import load_bundle, specification
from physics_guided_shared_mechanics.core import Budget, RateReplay
from physics_guided_rate_learning.run import apply_update, save_checkpoint
from physics_guided_rate_learning.verify import check_optimizer
from physics_guided_rate_learning.workflow import (
    specification as learning_specification,
)


class RateLearningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(specification(), 342)
        cls.recorded = load_npz(
            ROOT / "results/ootang_bplus_v1_9/20260911_state_pinn/model_342_0/R.npz"
        )
        prediction = load_npz(
            ROOT / "results/ootang_bplus_v1_11/20260911_shared_mechanics/full_342_0.npz"
        )
        cls.prediction = {k: prediction[k] for k in ARRAY_KEYS}

    def test_saved_replay_uses_the_same_complete_background_schema(self):
        first = mechanical_audit(self.bundle, self.prediction, self.recorded)
        second = mechanical_audit(
            self.bundle,
            {k: v.copy() for k, v in self.prediction.items()},
            {k: v.copy() for k, v in self.recorded.items()},
        )
        self.assertEqual(first, second)
        self.assertTrue(first["physical"]["passed"])
        self.assertEqual(len(first["physical"]["checks"]), 49)
        self.assertLess(first["mean_max_difference_mm"], 1e-8)

    def test_wrong_replay_background_or_physical_state_is_rejected(self):
        for key, ix in (("background", (10, 0)), ("current", (3, 0))):
            bad = {**self.recorded, key: self.recorded[key].copy()}
            bad[key][ix] += 0.01
            with self.assertRaises(ArithmeticError):
                mechanical_audit(self.bundle, self.prediction, bad)
        with self.assertRaises(ValueError):
            mechanical_audit(
                self.bundle, self.prediction, {**self.recorded, "unknown": np.zeros(1)}
            )

    def test_training_solver_contains_no_future_context(self):
        full = SimpleNamespace(
            coeff=np.zeros(318),
            force=np.arange(208.0).reshape(52, 4),
            elastic=np.zeros((52, 4)),
            lib=object(),
            reference={"future": 1},
            ctx=object(),
        )
        solver = training_solver(full, 36)
        self.assertEqual(
            set(vars(solver)), {"coeff", "force", "elastic", "lib", "records"}
        )
        self.assertEqual(solver.force.shape, (36, 4))
        full.force[:] += 100
        self.assertEqual(solver.force[0, 0], 0)

    def test_calibration_uses_only_the_registered_past_forecast_segment(self):
        means = np.zeros((3, 522, 4))
        labels = np.full((432, 4), 2.0)
        with patch(
            "physics_guided_rate_learning.workflow.components", return_value=means
        ):
            a = calibrate(Path("unused"), 432, labels)
            means[:, :342] = 1e6
            means[:, 432:] = -1e6
            labels[:342] = 1e6
            b = calibrate(Path("unused"), 432, labels)
        np.testing.assert_array_equal(a["sigma"], np.full((3, 4), 2.0))
        for key in a:
            np.testing.assert_array_equal(a[key], b[key])
        with self.assertRaises(ValueError):
            calibrate(Path("unused"), 432, labels[:431])

    def test_strict_success_requires_all_four_errors_for_each_point(self):
        rows = []
        for n in (432, 612):
            for strategy, value in (("P0", 1.0), ("R", 2.0), ("S", 0.5)):
                for station in POINTS:
                    for part in ("train", "prediction"):
                        rows.append(
                            dict(
                                outer_days=n,
                                strategy=strategy,
                                station=station,
                                part=part,
                                rmse_mm=2.0
                                if (n, strategy, station, part)
                                == (612, "S", "MJ3", "prediction")
                                else value,
                                mae_mm=value,
                                crps_mm=10.0 if strategy == "S" else value,
                            )
                        )
        result = compare(pd.DataFrame(rows))
        self.assertEqual(int(result.strict_mean_improvement.sum()), 7)
        self.assertFalse(
            result[(result.outer_days == 612) & (result.station == "MJ3")]
            .iloc[0]
            .strict_mean_improvement
        )

    def test_checkpoint_array_cropping_preserves_day_step_alignment(self):
        cropped = arrays(self.prediction, 342)
        self.assertEqual(cropped["state"].shape, (342, 24))
        self.assertEqual(cropped["masks"].shape, (341, 64))
        np.testing.assert_array_equal(cropped["mean"], self.prediction["mean"][:342])

    def test_optimizer_budget_rejects_before_backward_or_update(self):
        total, model, optimizer = Mock(), Mock(), Mock()
        budget = Budget({"neural_updates": 0, "reverse_passes": 1})
        with self.assertRaises(RuntimeError):
            apply_update(total, model, optimizer, budget, learning_specification())
        total.backward.assert_not_called()
        optimizer.step.assert_not_called()
        self.assertEqual(budget.counts["reverse_passes"], 0)

    def test_adam_checkpoint_roundtrip_and_setting_corruption(self):
        model = RateReplay()
        initial = {"state_dict": {k: v.clone() for k, v in model.state_dict().items()}}
        op = torch.optim.Adam(
            model.parameters(), lr=0.001, betas=(0.9, 0.999), eps=1e-8
        )
        case = SimpleNamespace(constants={"training_days": 342}, h=342)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "e0.pt"
            save_checkpoint(path, model, op, case, 0, 0, "synthetic metadata fixture")
            saved = torch.load(path, weights_only=True, map_location="cpu")
            check_optimizer(saved, initial, learning_specification(), 0)
            saved["optimizer_state"]["param_groups"][0]["lr"] = 0.01
            with self.assertRaises(ValueError):
                check_optimizer(saved, initial, learning_specification(), 0)
            with self.assertRaises(FileExistsError):
                save_checkpoint(path, model, op, case, 0, 0, "cannot overwrite")


if __name__ == "__main__":
    unittest.main()
