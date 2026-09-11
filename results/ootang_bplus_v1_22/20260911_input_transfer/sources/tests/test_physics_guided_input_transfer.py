"""Synthetic temporal, matching, scoring, and replay boundary checks."""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from physics_guided.features import Scaler
from physics_guided_history_learning.core import windows
from physics_guided_input_transfer.core import (
    build_tables,
    direction,
    match_inputs,
    squared_distances,
)
from physics_guided_input_transfer.verify import (
    Comparison,
    scalar_squared,
    verify_tables,
)
from physics_guided_input_transfer.workflow import specification


def synthetic_candidates():
    origins = np.repeat([100, 200], 180)
    targets = origins + np.tile(np.arange(180), 2)
    x = np.zeros((360, 30, 23, 1, 4))
    x[180:, :, 0] = 2
    q = np.zeros((180, 30, 23, 1, 4))
    q[:, :, 0] = 1.5
    return origins, targets, x, q


class TransferTests(unittest.TestCase):
    def test_joint_rms_and_channel_decomposition(self):
        a = np.zeros((30, 23, 1, 4))
        b = a.copy()
        b[:, 0, 0, 0] = 4
        total, group = squared_distances(a, b)
        self.assertAlmostEqual(total, 16 / (23 * 4))
        self.assertAlmostEqual(group[0], 2)
        self.assertAlmostEqual(total, np.sum(group * np.array([2, 14, 4, 2, 1])) / 23)

    def test_matches_same_lead_and_past_only(self):
        o, t, x, q = synthetic_candidates()
        d = match_inputs(400, o, t, x, q)
        np.testing.assert_array_equal(d["chosen"], np.arange(180, 360))
        self.assertTrue(
            all(t[k] - o[k] == lead and t[k] < 400 for lead, k in d["edges"])
        )
        self.assertEqual(len(d["edges"]), 360)

    def test_future_candidate_rejected(self):
        o, t, x, q = synthetic_candidates()
        with self.assertRaises(ValueError):
            match_inputs(300, o, t, x, q)

    def test_no_same_lead_candidate_is_explicit(self):
        o, t, x, q = synthetic_candidates()
        keep = (t - o) != 100
        with self.assertRaises(ValueError):
            match_inputs(400, o[keep], t[keep], x[keep], q)

    def test_tie_identity_ignores_input_order(self):
        o, t, x, q = synthetic_candidates()
        q[:, :, 0] = 1
        forward = match_inputs(400, o, t, x, q)
        reverse = match_inputs(400, o[::-1], t[::-1], x[::-1], q)
        np.testing.assert_array_equal(o[forward["chosen"]], np.full(180, 100))
        np.testing.assert_array_equal(o[::-1][reverse["chosen"]], np.full(180, 100))

    def test_one_joint_neighbor_for_all_points(self):
        o, t, x, q = synthetic_candidates()
        q[:, :, 0, 0, 0] = 0
        d = match_inputs(400, o, t, x, q)
        self.assertEqual(d["chosen"].shape, (180,))
        np.testing.assert_array_equal(o[d["chosen"]], np.full(180, 200))

    def test_single_candidate_has_missing_reference(self):
        o, t, x, q = synthetic_candidates()
        d = match_inputs(400, o[:180], t[:180], x[:180], q)
        self.assertTrue(np.isnan(d["past_reference"]).all())

    def test_zero_sign_is_not_counted_as_agreement(self):
        self.assertEqual(direction(0, 1), (False, False))
        self.assertEqual(direction(1e-6, 1), (False, False))
        self.assertEqual(direction(-2, -3), (True, True))
        self.assertEqual(direction(2, -3), (True, False))

    def test_full_scalar_distance_replay(self):
        rng = np.random.default_rng(0)
        a, b = rng.normal(size=(2, 30, 23, 1, 4))
        spec = specification()
        d, groups = squared_distances(a, b)
        sd, sg = scalar_squared(a, b, spec["groups"])
        np.testing.assert_allclose([d, *groups], [sd, *sg], rtol=1e-14, atol=1e-14)

    def test_input_history_ends_at_origin_and_scalers_are_not_refit(self):
        h = 100
        base = np.zeros((h + 180, 4))
        features = np.zeros((h + 180, 20, 4))
        labels = np.arange(h * 4, dtype=float).reshape(h, 4)
        physical = Scaler(np.zeros((20, 4)), np.ones((20, 4)), np.ones((20, 4)))
        history = Scaler(np.zeros((2, 4)), np.ones((2, 4)), np.ones((2, 4)))
        pairs = np.array([[70, 100], [70, 179]])
        changed = labels.copy()
        changed[70:] = -9999
        with patch.object(
            Scaler, "fit", side_effect=AssertionError("No scaler fitting")
        ):
            first = windows(
                base, features, labels, h, pairs, physical, history, "H"
            ).numpy()
            second = windows(
                base, features, changed, h, pairs, physical, history, "H"
            ).numpy()
        np.testing.assert_array_equal(first, second)
        np.testing.assert_array_equal(first[:, -1, 20, 0], np.tile(labels[69], (2, 1)))
        with self.assertRaises(ValueError):
            windows(
                base,
                features,
                np.vstack([labels, np.zeros((1, 4))]),
                h,
                pairs,
                physical,
                history,
                "H",
            )

    def test_probe_lock_unchanged_by_scoring_labels(self):
        o, t, x, q = synthetic_candidates()
        d = dict(
            origins=o,
            targets=t,
            candidate_correction=np.full((360, 4), 2.0),
            query_base=np.zeros((180, 4)),
            norm_mean=np.ones((180, 4)),
            **match_inputs(400, o, t, x, q),
        )
        chosen = d["chosen"].copy()
        first = build_tables({(400, "OOF"): d}, np.zeros((580, 4)))
        second = build_tables({(400, "OOF"): d}, np.ones((580, 4)) * 2)
        np.testing.assert_array_equal(d["chosen"], chosen)
        np.testing.assert_array_equal(
            first["queries"].probe_correction_mm, second["queries"].probe_correction_mm
        )
        self.assertEqual(len(second["metrics"]), 16)
        self.assertTrue(second["metrics"].forecast_improves_P0.all())
        self.assertTrue((second["metrics"].rmse_PROBE_mm == 0).all())
        self.assertTrue(first["metrics"].same_direction_fraction.isna().all())

    def test_missing_or_false_exact_evidence_rejected(self):
        comparison = Comparison()
        with self.assertRaises(AssertionError):
            comparison.array(np.array([1]), np.array([2]))
        with self.assertRaises(AssertionError):
            comparison.row({"passed": 1}, {"passed": True})

    def test_registered_budget_has_no_fit_or_network(self):
        s = specification()
        for key in (
            "neural_updates",
            "neural_forward_calls",
            "gradient_calls",
            "physical_forward_calls",
            "physical_optimizer_nfev",
            "regression_fits",
            "scaler_fits",
            "scale_fits",
        ):
            self.assertEqual(s[key], 0)
        self.assertEqual(
            (
                s["candidate_edges"],
                s["point_edges"],
                s["point_queries"],
                s["metric_rows"],
            ),
            (1800, 7200, 3600, 88),
        )
        self.assertEqual(s["neighbors"], 1)

    def test_all_registered_tables_have_independent_scalar_scores(self):
        spec = specification()
        prefixes = {}
        labels = np.arange(792 * 4, dtype=float).reshape(792, 4) / 10
        for h, n in [(342, 90), (432, 180), (612, 180)]:
            pairs = np.array(
                [
                    (o, t)
                    for o in (252, 342, 432)
                    if o < h
                    for t in range(o, min(o + 180, h))
                ]
            )
            x = np.zeros((len(pairs), 30, 23, 1, 4))
            q = np.zeros((n, 30, 23, 1, 4))
            for strategy in ("IN", "OOF"):
                d = match_inputs(h, pairs[:, 0], pairs[:, 1], x, q)
                prefixes[(h, strategy)] = dict(
                    origins=pairs[:, 0],
                    targets=pairs[:, 1],
                    candidate_correction=np.full((len(pairs), 4), 2.0),
                    query_base=np.zeros((n, 4)),
                    norm_mean=np.ones((n, 4)),
                    **d,
                )
        tables = build_tables(prefixes, labels)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            source = out / "old"
            source.mkdir()
            spec["source_run"] = str(source)
            old_rows = []
            for r in tables["metrics"].to_dict("records"):
                if r["segment"] != "all" or r["fit_days"] == 342:
                    continue
                for strategy, group in [(r["strategy"], "NORM"), ("P0", "P0")]:
                    old_rows.append(
                        dict(
                            outer_days=r["fit_days"],
                            strategy=strategy,
                            station=r["station"],
                            part="prediction",
                            rmse_mm=r[f"rmse_{group}_mm"],
                            mae_mm=r[f"mae_{group}_mm"],
                        )
                    )
            pd.DataFrame(old_rows).to_csv(source / "metrics.csv", index=False)
            for name, table in tables.items():
                table.to_csv(out / f"{name}.csv", index=False)
            counts = verify_tables(out, prefixes, labels, spec, Comparison())
            self.assertEqual(counts, dict(edges=7200, queries=3600, metrics=88))
            tampered = tables["metrics"].copy()
            tampered.loc[0, "rmse_PROBE_mm"] += 1
            tampered.to_csv(out / "metrics.csv", index=False)
            with self.assertRaises(AssertionError):
                verify_tables(out, prefixes, labels, spec, Comparison())


if __name__ == "__main__":
    unittest.main()
