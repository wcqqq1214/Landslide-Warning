"""Synthetic scientific-contract checks; no experiment data fitting or physical calls."""

import copy
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import joblib
import numpy as np
import pandas as pd
from scipy.stats import norm
from threadpoolctl import threadpool_limits

from physics_guided_gp import (
    CONFIG,
    POINTS,
    BoundedOptimizer,
    decide,
    independent_posterior,
    new_gp,
    predict,
    prepare_inputs,
    raw_features,
    read_labels,
    score_distributions,
)
from verify_ootang_bplus_gp import compare_frame


class GPContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.threads = threadpool_limits(limits=1)
        cls.threads.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.threads.__exit__(None, None, None)

    def setUp(self):
        self.spec = json.loads(CONFIG.read_text())

    def test_prefix_reader_does_not_parse_future_displacements(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data.csv"
            frame = pd.DataFrame(
                {"Date": pd.date_range("2016-07-01", periods=1169).strftime("%Y-%m-%d")}
            )
            for point in POINTS:
                frame[point + "/mm"] = ["2"] * 792 + ["forbidden future label"] * 377
            frame.to_csv(path, index=False)
            np.testing.assert_array_equal(
                read_labels(path, 792), np.full((792, 4), 2.0)
            )
            with self.assertRaises(ValueError):
                read_labels(path, 1168)
            with self.assertRaises(ValueError):
                read_labels(path, 1461)

    def test_feature_definitions_and_order(self):
        saved = dict(
            mean=np.zeros((3, 4)),
            forcing=np.array([[0, 10], [0, 13], [0, 12.0]]),
            moisture=np.arange(12.0).reshape(3, 4),
            rain_head=np.ones((3, 4)) * 4,
            reservoir_head=np.array([12, 14, 16]),
        )
        expected = np.array([[0, 1.5, 4, 2, 0], [1, 5.5, 4, 1, 3], [2, 9.5, 4, 4, -1]])
        np.testing.assert_array_equal(raw_features(saved), expected)

    def test_normalization_uses_only_training_prefix_and_no_target_centering(self):
        raw = np.arange(1168 * 5, dtype=float).reshape(1168, 5)
        raw[:, 4] = 3
        base = np.zeros((1168, 4))
        labels = np.tile([2.0, 0.0, -4.0, 1e-9], (792, 1))
        a = prepare_inputs(raw, base, labels, self.spec)
        other = raw.copy()
        other[:30] = -1e7
        other[792:] = 1e9
        b = prepare_inputs(other, base, labels, self.spec)
        self.assertEqual(a.normalizers, b.normalizers)
        np.testing.assert_array_equal(a.targets, b.targets)
        np.testing.assert_allclose(a.x[30:792, :4].mean(0), 0, atol=1e-12)
        np.testing.assert_allclose(a.x[30:792, :4].std(0), 1, atol=1e-12)
        np.testing.assert_array_equal(a.targets[:, 0], np.ones(762))
        self.assertEqual(
            a.normalizers["residual_floor_used"], [False, True, False, True]
        )
        self.assertEqual(a.normalizers["feature_denominator"][4], 1)
        with self.assertRaises(ValueError):
            prepare_inputs(raw, base, np.zeros((1168, 4)), self.spec)

    def test_observation_noise_once_and_jitter_training_only_with_reload(self):
        spec = copy.deepcopy(self.spec)
        spec["jitter"] = 0.2
        spec["kernel"]["noise_variance"] = 0.1
        train = np.arange(15.0).reshape(3, 5) / 20
        target = np.array([0.1, -0.2, 0.5])
        x = np.concatenate([train, train + 0.2])
        gp = new_gp(spec, None).fit(train, target)
        scale = 7.0
        result, _ = predict(gp, x, np.ones(len(x)) * 20, scale, spec)
        mu, latent, obs = independent_posterior(
            train, target, x, 1.0, np.ones(5), 0.1, 0.2
        )
        np.testing.assert_allclose(result["mean"], 20 + mu * scale, atol=1e-12)
        np.testing.assert_allclose(
            result["latent_variance"], latent * scale**2, atol=1e-12
        )
        np.testing.assert_allclose(
            result["observation_variance"], obs * scale**2, atol=1e-12
        )
        np.testing.assert_allclose(
            result["observation_variance"] - result["latent_variance"],
            0.1 * scale**2,
            atol=1e-12,
        )
        self.assertFalse(
            np.allclose(result["observation_variance"], (latent + 0.1 + 0.2) * scale**2)
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model.joblib"
            joblib.dump(gp, path)
            loaded, _ = predict(joblib.load(path), x, np.ones(len(x)) * 20, scale, spec)
            for key in result:
                np.testing.assert_array_equal(result[key], loaded[key])

    def test_zero_residual_retains_bplus_mean(self):
        x = np.arange(30.0).reshape(6, 5) / 10
        base = np.arange(6.0) * 100
        gp = new_gp(self.spec, None).fit(x, np.zeros(6))
        result, _ = predict(gp, x, base, 10.0, self.spec)
        np.testing.assert_array_equal(result["mean"], base)
        np.testing.assert_array_equal(result["residual_mean"], np.zeros(6))

    def test_optimizer_converges_once_on_synthetic_objective(self):
        recorder = []
        opt = BoundedOptimizer(self.spec, record=lambda k, d: recorder.append((k, d)))

        def objective(x, eval_gradient):
            self.assertTrue(eval_gradient)
            return float(np.sum((x - 0.2) ** 2)), 2 * (x - 0.2)

        theta, loss = opt(
            objective, np.array([1.0, -1.0]), np.array([[-2.0, 2.0], [-2.0, 2.0]])
        )
        np.testing.assert_allclose(theta, 0.2, atol=1e-9)
        self.assertLess(loss, 1e-12)
        self.assertTrue(opt.result["success"])
        self.assertEqual(
            opt.calls, sum(k == "objective_requested" for k, d in recorder)
        )
        with self.assertRaisesRegex(RuntimeError, "restarts"):
            opt(objective, theta, np.array([[-2.0, 2.0], [-2.0, 2.0]]))

    def test_hard_objective_budget_stops_before_extra_call(self):
        spec = copy.deepcopy(self.spec)
        spec["optimizer"]["max_evaluations"] = 1
        seen = []

        def objective(x, eval_gradient):
            seen.append(x.copy())
            return float(np.sum(x * x)), 2 * x

        opt = BoundedOptimizer(spec)
        with self.assertRaisesRegex(RuntimeError, "limit"):
            opt(objective, np.array([1.0]), np.array([[-2.0, 2.0]]))
        self.assertEqual(len(seen), 1)
        self.assertEqual(opt.calls, 1)

    def test_optimizer_nonconvergence_is_not_accepted(self):
        from scipy.optimize import OptimizeResult

        result = OptimizeResult(
            success=False,
            status=1,
            message="limit",
            nit=0,
            nfev=0,
            fun=1.0,
            x=np.array([0.0]),
        )
        with patch("physics_guided_gp.minimize", return_value=result):
            opt = BoundedOptimizer(self.spec)
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                opt(
                    lambda x, eval_gradient: (1.0, x),
                    np.zeros(1),
                    np.array([[-1.0, 1.0]]),
                )

    def sample_scores(self):
        # Equal point counts, unequal errors make point-mean RMSE differ from pooled RMSE.
        labels = np.tile([1.0, 2.0, 3.0, 4.0], (1168, 1))
        base = np.zeros((1168, 4))
        dates = (
            pd.date_range("2016-07-01", periods=1168).strftime("%Y-%m-%d").to_numpy()
        )
        saved = dict(mean=base, dates=dates)
        old = dict(
            means=np.tile(base[None], (3, 1, 1)), sigmas=np.ones((3, 1168, 4)) * 2
        )
        pred = dict(mean=base + 0.5, observation_variance=np.ones_like(base) * 4)
        return score_distributions(saved, old, pred, labels)

    def test_scores_match_single_gaussian_formula_and_distinguish_aggregations(self):
        metrics, aggregate, daily = self.sample_scores()
        row = metrics.query(
            "strategy == 'BPLUS_GP' and part == 'prediction' and station == 'ATU1'"
        ).iloc[0]
        z = 0.5 / 2
        expected = 2 * (
            z * (2 * norm.cdf(z) - 1) + 2 * norm.pdf(z) - 1 / np.sqrt(np.pi)
        )
        self.assertAlmostEqual(row.crps_mm, expected, places=12)
        self.assertAlmostEqual(row.width_90_mm, 4 * norm.ppf(0.95), places=5)
        base = aggregate.query("strategy == 'M0' and part == 'prediction'").set_index(
            "aggregation"
        )
        self.assertAlmostEqual(base.loc["point_mean", "rmse_mm"], 2.5)
        self.assertAlmostEqual(base.loc["pooled", "rmse_mm"], np.sqrt(7.5))
        self.assertTrue(metrics.query("strategy == 'M0'").crps_mm.isna().all())
        self.assertEqual((len(metrics), len(aggregate), len(daily)), (24, 12, 13656))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "scores.csv"
            metrics.to_csv(path, index=False)
            compare_frame(metrics, path, self.spec)
            changed = metrics.copy()
            changed.loc[0, "mae_mm"] += 1e-3
            with self.assertRaises(AssertionError):
                compare_frame(changed, path, self.spec)

    def passing_scores(self):
        metrics, _, _ = self.sample_scores()
        mean_cols = ["mae_mm", "rmse_mm"]
        probability_cols = ["crps_mm", "interval_score_90_mm"]
        metrics.loc[metrics.strategy != "BPLUS_GP", mean_cols] = 10.0
        metrics.loc[metrics.strategy == "BPLUS_GP", mean_cols] = 9.0
        metrics.loc[metrics.strategy == "v1.1-e0", probability_cols] = 10.0
        metrics.loc[metrics.strategy == "BPLUS_GP", probability_cols] = 9.0
        metrics.loc[metrics.strategy != "M0", "coverage_90"] = 0.8
        metrics.loc[metrics.strategy != "M0", "width_90_mm"] = 10.0
        return metrics

    def test_fixed_gates_reject_worse_point_even_with_better_average(self):
        metrics = self.passing_scores()
        self.assertTrue(decide(metrics, self.spec)["effect_passed"])
        mask = (
            (metrics.strategy == "BPLUS_GP")
            & (metrics.part == "prediction")
            & (metrics.station == "ATU1")
        )
        metrics.loc[mask, "rmse_mm"] = 10.1
        result = decide(metrics, self.spec)
        self.assertTrue(result["gates"]["prediction_rmse_mm_improved"])
        self.assertFalse(result["effect_passed"])
        self.assertFalse(result["points"][0]["prediction_rmse_mm_guard"])

    def test_coverage_slack_and_width_protection(self):
        metrics = self.passing_scores()
        mask = (
            (metrics.strategy == "BPLUS_GP")
            & (metrics.part == "prediction")
            & (metrics.station == "ATU1")
        )
        metrics.loc[mask, "coverage_90"] = 0.8 - 1 / 376
        metrics.loc[mask, "width_90_mm"] = 11.0
        self.assertTrue(decide(metrics, self.spec)["effect_passed"])
        metrics.loc[mask, "coverage_90"] -= 1 / 376
        self.assertFalse(decide(metrics, self.spec)["points"][0]["coverage_guard"])
        metrics.loc[mask, "coverage_90"] = 0.8
        metrics.loc[mask, "interval_score_90_mm"] = 10.0
        self.assertFalse(decide(metrics, self.spec)["points"][0]["width_guard"])
        metrics = self.passing_scores()
        metrics.loc[metrics.strategy == "M0", "crps_mm"] = 2.0
        with self.assertRaises(ValueError):
            decide(metrics, self.spec)

    def test_single_run_lock_order_and_failure_does_not_read_development(self):
        import run_ootang_bplus_gp as runner
        from physics_guided_gp import kernel

        class SyntheticModel:
            def __init__(self, spec, optimizer):
                self.kernel = kernel(spec)
                self.optimizer = optimizer
                self.log_marginal_likelihood_value_ = 0.0

            def fit(self, x, y):
                self.optimizer.result = dict(success=True)
                return self

        saved = dict(
            mean=np.zeros((1168, 4)),
            moisture=np.zeros((1168, 4)),
            rain_head=np.zeros((1168, 4)),
            reservoir_head=np.zeros(1168),
            forcing=np.zeros((1168, 2)),
            dates=pd.date_range("2016-07-01", periods=1168)
            .strftime("%Y-%m-%d")
            .to_numpy(),
        )
        old = dict(means=np.zeros((3, 1168, 4)), sigmas=np.ones((3, 1168, 4)))
        for fail_point in (None, 2):
            with (
                self.subTest(fail_point=fail_point),
                tempfile.TemporaryDirectory() as folder,
                ExitStack() as stack,
            ):
                spec = copy.deepcopy(self.spec)
                spec["output_dir"] = str(Path(folder) / "formal")
                spec["implementation_deadline_utc"] = (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat()
                spec["training_deadline_utc"] = (
                    datetime.now(timezone.utc) + timedelta(hours=2)
                ).isoformat()
                stack.enter_context(
                    patch.object(runner, "specification", return_value=spec)
                )
                stack.enter_context(
                    patch.object(
                        runner,
                        "committed_snapshot",
                        return_value={"commit": "synthetic", "files": {}},
                    )
                )
                stack.enter_context(
                    patch.object(
                        runner, "load_reference", return_value=(saved, old, {})
                    )
                )
                stack.enter_context(
                    patch.object(
                        runner,
                        "check_historical_scores",
                        return_value={"synthetic": True},
                    )
                )
                stack.enter_context(patch("builtins.print"))
                calls = []

                def make_gp(spec, optimizer):
                    calls.append("fit")
                    if len(calls) == fail_point:
                        raise RuntimeError("synthetic point failure")
                    return SyntheticModel(spec, optimizer)

                stack.enter_context(patch.object(runner, "new_gp", side_effect=make_gp))

                def dump(model, path, compress):
                    Path(path).write_bytes(b"synthetic model")

                stack.enter_context(
                    patch.object(runner.joblib, "dump", side_effect=dump)
                )

                def fake_predict(model, x, base, scale, spec):
                    return dict(
                        mean=base,
                        residual_mean=base,
                        latent_variance=np.ones(len(x)),
                        observation_variance=np.ones(len(x)) * 2,
                        noise_variance=np.ones(len(x)),
                    ), {}

                stack.enter_context(
                    patch.object(runner, "predict", side_effect=fake_predict)
                )
                reads = []

                def fake_labels(path, rows):
                    reads.append(rows)
                    if rows == 1168:
                        self.assertEqual(len(calls), 4)
                        self.assertTrue(
                            (Path(spec["output_dir"]) / "prediction_lock.json").exists()
                        )
                    return np.zeros((rows, 4))

                stack.enter_context(
                    patch.object(runner, "read_labels", side_effect=fake_labels)
                )
                result = runner.main()
                self.assertEqual(result, 0 if fail_point is None else 1)
                self.assertEqual(reads, [792, 1168] if fail_point is None else [792])
                with self.assertRaises(FileExistsError):
                    runner.main()


if __name__ == "__main__":
    unittest.main()
