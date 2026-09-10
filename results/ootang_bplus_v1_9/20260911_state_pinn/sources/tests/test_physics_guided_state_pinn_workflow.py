from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from physics_guided_sample_learning.core import score_components
from physics_guided_state_pinn.core import StatePINN, background_delta, tensor
from physics_guided_state_pinn.verify import NumericalChecks, metric_checks
from physics_guided_state_pinn.workflow import (
    POINTS,
    load_bundle,
    make_case,
    projected_background,
    read_labels,
    specification,
)


class StatePinnWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)
        cls.spec = specification()

    def test_registered_training_cases_exclude_future_states_and_reproduce_bplus(self):
        for h in self.spec["prefixes"]:
            with self.subTest(prefix=h):
                bundle = load_bundle(self.spec, h, h)
                case = make_case(bundle, h)
                self.assertEqual(case.days, h)
                self.assertEqual(case.base.shape, ((h - 1) * 64 + 1, 24))
                torch.manual_seed(0)
                model = StatePINN()
                with torch.no_grad():
                    result = model(case)
                np.testing.assert_allclose(
                    result["mean"].numpy(), bundle[0]["mean"], rtol=0, atol=1e-8
                )
                self.assertEqual(
                    sum(p.numel() for p in model.parameters()), self.spec["parameters"]
                )

    def test_labels_are_parsed_only_through_the_registered_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            frame = pd.DataFrame({"Date": pd.date_range("2016-07-01", periods=792)})
            for j, point in enumerate(POINTS):
                frame[point + "/mm"] = [str(j + 1)] * 342 + [
                    "unavailable future label"
                ] * 450
            frame.to_csv(path, index=False)
            labels = read_labels(path, 342)
            np.testing.assert_array_equal(
                labels, np.tile([1.0, 2.0, 3.0, 4.0], (342, 1))
            )
            with self.assertRaises(ValueError):
                read_labels(path, 432)

    def test_replay_background_preserves_the_original_kelvin_part(self):
        saved = load_bundle(self.spec, 342, 342)[0]
        gamma = np.full((342, 4), 1.1)
        gamma[100:200, 0] = 0.75
        delta = background_delta(
            tensor(saved["background_rate"]), tensor(gamma)
        ).numpy()
        result = projected_background(
            saved, {"multiplier": gamma, "delta_background": delta}
        )
        expected = saved["background"] + np.vstack(
            [
                np.zeros(4),
                np.cumsum(saved["background_rate"][1:] * (gamma[1:] - 1), axis=0),
            ]
        )
        np.testing.assert_array_equal(result, expected)
        np.testing.assert_array_equal(result[0], saved["background"][0])
        with self.assertRaises(ValueError):
            projected_background(
                saved, {"multiplier": gamma, "delta_background": delta + 1}
            )

    def test_independent_gaussian_mixture_and_seed_metrics_detect_corruption(self):
        n, days = 432, 612
        t = np.arange(days)[:, None]
        truth = 0.2 * t + np.array([1.0, 2.0, 3.0, 4.0])
        means = np.stack(
            [truth + (k - 1) * 0.3 + np.sin(t / 17) * 0.1 for k in range(3)]
        )
        scales = np.array(
            [[0.001, 0.5, 2.0, 4.0], [0.3, 1.0, 2.0, 3.0], [0.6, 2.0, 3.0, 4.0]]
        )
        rows, distribution = score_components(means, scales, truth, n, "R")
        frame = pd.DataFrame(rows)
        check = NumericalChecks()
        metric_checks(check, means, scales, truth, n, frame, distribution)
        for seed in range(3):
            seed_rows, _ = score_components(
                means[seed : seed + 1], scales[seed : seed + 1], truth, n, "R"
            )
            metric_checks(
                check,
                means[seed : seed + 1],
                scales[seed : seed + 1],
                truth,
                n,
                pd.DataFrame(seed_rows),
            )
        self.assertGreater(check.values, 1000)
        changed = frame.copy()
        changed.loc[0, "crps_mm"] += 0.1
        with self.assertRaises(ArithmeticError):
            metric_checks(
                NumericalChecks(), means, scales, truth, n, changed, distribution
            )


if __name__ == "__main__":
    unittest.main()
