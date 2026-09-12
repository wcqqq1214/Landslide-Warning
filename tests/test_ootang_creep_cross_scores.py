from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "code"))
from audit_ootang_creep_cross_scores import contrasts, score_matrix, state_differences
from physics_guided import probability


def fixture():
    y = np.zeros((36, 4))
    mu = np.broadcast_to(np.array([-4.0, 0.0, 8.0])[:, None, None], (3, 36, 4)).copy()
    sigma = np.broadcast_to(np.array([0.2, 2.0, 5.0])[:, None, None], mu.shape).copy()
    means = dict(bplus=np.zeros_like(mu), neural=mu, replay=mu / 2)
    scales = dict(e0=np.ones_like(mu), v23=sigma)
    return y, means, scales


class CrossScoreTests(unittest.TestCase):
    def test_state_comparison_uses_every_substep_on_the_scored_days(self):
        n = 36
        steps = (n - 1) * 64
        states = np.broadcast_to(np.arange(steps + 1)[:, None], (steps + 1, 24))
        means = np.broadcast_to(np.arange(n)[None, :, None], (3, n, 4))
        selected = dict(means=means, reference_means=np.zeros_like(means))

        def read_saved(path):
            if path.name == "prediction.npz":
                return dict(states=states)
            return dict(current=np.zeros((steps, 24)))

        with patch(
            "audit_ootang_creep_cross_scores.frozen.read_npz", side_effect=read_saved
        ):
            table = state_differences(dict(dates=np.arange(n)), selected, 33)
        for phase, first, last in (("train", 1857, 2048), ("prediction", 2049, 2240)):
            subset = table[(table.phase == phase) & (table.quantity != "observed_mean")]
            self.assertTrue((subset.samples == 192).all())
            self.assertTrue((subset.mean_absolute == (first + last) / 2).all())
        daily = table[
            (table.phase == "prediction") & (table.quantity == "observed_mean")
        ]
        self.assertTrue((daily.samples == 3).all())
        self.assertTrue((daily.mean_absolute == 34).all())

    def test_complete_matrix_keeps_seed_pairing_and_unchanged_mean_scores(self):
        y, means, scales = fixture()
        table = score_matrix(means, scales, y, 33, probability)
        self.assertEqual(len(table), 732)
        self.assertEqual(set(table.days), {3})
        delta = contrasts(table)
        self.assertEqual(len(delta), 854)
        self.assertTrue(
            (
                delta.loc[
                    (delta.contrast == "scale_v23_minus_e0")
                    & delta.metric.isin(["mae", "rmse"]),
                    "delta",
                ]
                == 0
            ).all()
        )

        def crps(t):
            return t.loc[
                (t.mean_source == "neural")
                & (t.scale_source == "v23")
                & (t.phase == "prediction")
                & (t.station == "point_mean")
                & (t.metric == "crps"),
                "value",
            ].item()

        perm = [2, 0, 1]
        paired = score_matrix(
            {k: v[perm] for k, v in means.items()},
            {k: v[perm] for k, v in scales.items()},
            y,
            33,
            probability,
        )
        self.assertAlmostEqual(crps(table), crps(paired), places=12)
        unpaired = score_matrix(
            means, {k: v[perm] for k, v in scales.items()}, y, 33, probability
        )
        self.assertGreater(abs(crps(table) - crps(unpaired)), 0.01)
        collapsed = probability.crps(
            means["neural"].mean(axis=0, keepdims=True),
            scales["v23"].mean(axis=0, keepdims=True),
            y,
        )[33:].mean()
        self.assertGreater(abs(crps(table) - collapsed), 0.01)

    def test_missing_scored_dates_or_seeds_are_rejected_not_dropped(self):
        y, means, scales = fixture()
        scales["e0"][:, :29] = np.nan  # Original pre-warmup missingness is excluded.
        score_matrix(means, scales, y, 33, probability)
        means["neural"][1, 35, 2] = np.nan
        with self.assertRaises(ValueError):
            score_matrix(means, scales, y, 33, probability)
        y, means, scales = fixture()
        means["neural"] = means["neural"][:2]
        with self.assertRaises(ValueError):
            score_matrix(means, scales, y, 33, probability)


if __name__ == "__main__":
    unittest.main()
