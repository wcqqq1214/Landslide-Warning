"""Chronological mean-error supervision and frozen mean invariants for C5."""

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
from rolling_probability.crossfit_scale import historical_queries, refit_scale
from rolling_probability.data import training_examples
from rolling_probability.ridge import dynamic_features, fit_distribution, transfer
from test_rolling_probability import synthetic


class CrossfitScaleContracts(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads(
            (ROOT / "config/ootang_rolling_probability.v3_4.json").read_text()
        )

    def test_historical_queries_do_not_cross_fold_and_ignore_later_labels(self):
        y, teacher = synthetic(n=400)
        pool = {100: teacher}
        original = historical_queries(y, pool, self.spec, 180, 240)
        np.testing.assert_array_equal(original["origins"], np.arange(180, 211))
        self.assertEqual(original["origins"][-1] + 29, 239)
        changed = y.copy()
        changed[240:] = 1e10
        repeated = historical_queries(changed, pool, self.spec, 180, 240)
        for key in ("x", "z", "y", "anchor", "origins", "teachers"):
            np.testing.assert_array_equal(original[key], repeated[key])
        changed[200:240] = -1e10
        poisoned = historical_queries(changed, pool, self.spec, 180, 240)
        np.testing.assert_array_equal(original["x"][:21], poisoned["x"][:21])
        with self.assertRaisesRegex(ValueError, "available label prefix"):
            historical_queries(y[:230], pool, self.spec, 180, 240)

    def test_refitting_historical_scale_cannot_change_the_mean(self):
        y, teacher = synthetic(n=400)
        data = training_examples(
            y[:180], {100: teacher}, extra_baselines=self.spec["extra_baselines"]
        )
        original = fit_distribution(data, 0.001, "FULL", self.spec)
        held = historical_queries(y, {100: teacher}, self.spec, 180, 240)
        experts = np.stack(
            [held["baselines"][k] for k in self.spec["expert_names"]], axis=2
        )
        _, _, q = dynamic_features(held["x"], experts)
        before, old_scale = original.predict_raw(
            held["x"], held["z"], held["anchor"], experts
        )
        adapted = refit_scale(original, held["y"] - before, q, self.spec)
        after, new_scale = adapted.predict_raw(
            held["x"], held["z"], held["anchor"], experts
        )
        np.testing.assert_array_equal(before, after)
        for key in self.spec["crossfit"]["mean_fields_frozen"]:
            self.assertEqual(original.state[key], adapted.state[key])
        self.assertTrue(np.isfinite(new_scale).all())
        self.assertFalse(np.array_equal(old_scale, new_scale))
        self.assertNotIn("scale_training_mode", original.state)

    def test_development_only_candidate_cannot_read_transfer_labels(self):
        with patch(
            "rolling_probability.ridge.read_prefix",
            side_effect=AssertionError("read labels"),
        ):
            with self.assertRaisesRegex(ValueError, "development-only"):
                transfer({}, self.spec, None, None, None)


if __name__ == "__main__":
    unittest.main()
