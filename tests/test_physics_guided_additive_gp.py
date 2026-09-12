"""Synthetic checks for the v2 plan; no real-data GP fitting."""

from contextlib import ExitStack
import copy
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
from sklearn.base import clone
from threadpoolctl import threadpool_limits

from physics_guided_gp import BoundedOptimizer, prepare_inputs, read_labels
from physics_guided_additive_gp import (
    ARMS,
    CONFIG,
    POINTS,
    compare_frame,
    decide,
    independent_posterior,
    kernel,
    new_gp,
    predict,
    score_all,
)


class AdditiveContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.threads = threadpool_limits(1)
        cls.threads.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.threads.__exit__(None, None, None)

    def setUp(self):
        self.spec = json.loads(CONFIG.read_text())
        self.rng = np.random.default_rng(43)
        self.x = self.rng.normal(size=(9, 5))

    def test_selected_columns_clone_and_hyperparameter_gradients(self):
        for arm in ARMS:
            k = kernel(self.spec, arm)
            self.assertEqual(len(k.theta), self.spec["hyperparameter_counts"][arm])
            np.testing.assert_array_equal(clone(k).theta, k.theta)
            value, gradient = k(self.x, eval_gradient=True)
            np.testing.assert_allclose(value, value.T, atol=1e-14)
            np.testing.assert_allclose(k.diag(self.x), np.diag(value), atol=1e-14)
            self.assertGreater(np.linalg.eigvalsh(value).min(), 0)
            step = self.spec["gradient_difference_step"]
            for i in range(len(k.theta)):
                plus, minus = k.theta.copy(), k.theta.copy()
                plus[i] += step
                minus[i] -= step
                fd = (
                    k.clone_with_theta(plus)(self.x) - k.clone_with_theta(minus)(self.x)
                ) / (2 * step)
                np.testing.assert_allclose(
                    gradient[:, :, i],
                    fd,
                    atol=self.spec["gradient_atol"],
                    rtol=self.spec["gradient_rtol"],
                )
        k = kernel(self.spec, "ADD")
        time, physical = k.k1.k1, k.k1.k2
        changed = self.x.copy()
        changed[:, 1:] += self.rng.normal(size=(9, 4)) * 100
        np.testing.assert_array_equal(time(changed), time(self.x))
        changed = self.x.copy()
        changed[:, 0] += np.arange(9) * 100
        np.testing.assert_array_equal(physical(changed), physical(self.x))
        time_only = kernel(self.spec, "TIME_ONLY")
        changed = self.x.copy()
        changed[:, 1:] *= 100
        np.testing.assert_array_equal(time_only(changed), time_only(self.x))

    def test_time_only_is_structural_removal_not_add_time_posterior(self):
        target = self.rng.normal(size=9)
        add_kernel = kernel(self.spec, "ADD")
        removed = add_kernel.k1.k1 + add_kernel.k2
        np.testing.assert_array_equal(
            removed(self.x), kernel(self.spec, "TIME_ONLY")(self.x)
        )
        gp = new_gp(self.spec, "TIME_ONLY", None).fit(self.x, target)
        direct = new_gp(self.spec, "TIME_ONLY", None)
        direct.kernel = removed
        direct.fit(self.x, target)
        np.testing.assert_array_equal(gp.predict(self.x), direct.predict(self.x))
        add = new_gp(self.spec, "ADD", None).fit(self.x, target)
        p, _ = predict(add, "ADD", self.x, np.zeros(9), 1.0, self.spec)
        self.assertGreater(abs(p["time_mean"] - gp.predict(self.x)).max(), 1e-4)

    def test_joint_posterior_cross_term_noise_units_and_reload(self):
        spec = copy.deepcopy(self.spec)
        spec["jitter"] = 0.2
        spec["kernel"]["noise_variance"] = 0.1
        target = self.rng.normal(size=9)
        query = np.r_[self.x, self.x + 0.2]
        for arm in ARMS:
            gp = new_gp(spec, arm, None).fit(self.x, target)
            p, audit = predict(gp, arm, query, np.full(18, 20.0), 7.0, spec)
            manual = independent_posterior(self.x, target, query, audit, spec["jitter"])
            for key, value in manual.items():
                np.testing.assert_allclose(
                    p[key],
                    value * (7 if key.endswith("mean") else 49),
                    rtol=1e-10,
                    atol=1e-10,
                )
            np.testing.assert_allclose(
                p["mean"], 20 + p["time_mean"] + p["physical_mean"], atol=1e-11
            )
            np.testing.assert_allclose(
                p["latent_variance"],
                p["time_variance"] + p["physical_variance"] + 2 * p["cross_covariance"],
                atol=1e-11,
            )
            np.testing.assert_allclose(
                p["observation_variance"] - p["latent_variance"], 4.9, atol=1e-11
            )
            if arm == "ADD":
                self.assertGreater(abs(p["cross_covariance"]).max(), 1.0)
                self.assertFalse(
                    np.allclose(
                        p["latent_variance"],
                        p["time_variance"] + p["physical_variance"],
                    )
                )
            else:
                for key in ("physical_mean", "physical_variance", "cross_covariance"):
                    np.testing.assert_array_equal(p[key], np.zeros(18))
            with tempfile.TemporaryDirectory() as folder:
                file = Path(folder) / "gp.joblib"
                joblib.dump(gp, file)
                reloaded, _ = predict(
                    joblib.load(file), arm, query, np.full(18, 20.0), 7.0, spec
                )
                for key in p:
                    np.testing.assert_array_equal(p[key], reloaded[key])

    def test_zero_residual_returns_bplus(self):
        for arm in ARMS:
            gp = new_gp(self.spec, arm, None).fit(self.x, np.zeros(9))
            base = np.arange(9.0) * 200
            p, _ = predict(gp, arm, self.x, base, 3.0, self.spec)
            np.testing.assert_array_equal(p["mean"], base)
            np.testing.assert_array_equal(p["physical_mean"], np.zeros(9))

    def test_optimizer_log_roundtrip_is_exact_in_stored_representation(self):
        from verify_ootang_bplus_additive_gp import check_optimizer_kernel

        for arm in ARMS:
            k = kernel(self.spec, arm)
            optimum = np.full(k.n_dims, 0.1)
            fitted = k.clone_with_theta(optimum)
            self.assertGreater(abs(fitted.theta - optimum).max(), 0)
            check_optimizer_kernel(fitted, arm, optimum, self.spec)
            changed = optimum.copy()
            changed[0] += 0.001
            with self.assertRaises(AssertionError):
                check_optimizer_kernel(
                    k.clone_with_theta(changed), arm, optimum, self.spec
                )

    def test_joint_fit_with_bounded_optimizer_is_one_call(self):
        for arm in ARMS:
            opt = BoundedOptimizer(self.spec)
            gp = new_gp(self.spec, arm, opt).fit(self.x, np.sin(self.x[:, 0]))
            self.assertTrue(opt.result["success"])
            self.assertEqual(opt.invocations, 1)
            self.assertLessEqual(opt.calls, 400)
            self.assertEqual(
                len(gp.kernel_.theta), self.spec["hyperparameter_counts"][arm]
            )

    def test_target_sign_normalization_and_label_prefix(self):
        raw = np.arange(1168 * 5, dtype=float).reshape(1168, 5)
        base = np.full((1168, 4), 10.0)
        labels = np.tile([11.0, 8.0, 13.0, 10.0], (792, 1))
        a = prepare_inputs(raw, base, labels, self.spec)
        changed = raw.copy()
        changed[792:] = 1e9
        b = prepare_inputs(changed, base, labels, self.spec)
        self.assertEqual(a.normalizers, b.normalizers)
        np.testing.assert_array_equal(a.targets[0], [1.0, -1.0, 1.0, 0.0])
        with self.assertRaises(ValueError):
            prepare_inputs(raw, base, np.ones((1168, 4)), self.spec)
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "labels.csv"
            frame = pd.DataFrame(
                {"Date": pd.date_range("2016-07-01", periods=1461).strftime("%Y-%m-%d")}
            )
            for p in POINTS:
                frame[p + "/mm"] = ["1"] * 792 + ["not allowed"] * (1461 - 792)
            frame.to_csv(file, index=False)
            np.testing.assert_array_equal(read_labels(file, 792), np.ones((792, 4)))
            with self.assertRaises(ValueError):
                read_labels(file, 1461)

    def sample(self):
        base = np.zeros((1168, 4))
        saved = dict(
            mean=base,
            dates=pd.date_range("2016-07-01", periods=1168)
            .strftime("%Y-%m-%d")
            .to_numpy(),
        )
        old = dict(means=np.zeros((3, 1168, 4)), sigmas=np.ones((3, 1168, 4)) * 2)
        historical = dict(mean=base + 0.1, observation_variance=np.ones_like(base) * 4)
        pred = {
            arm: dict(mean=base + 0.5, observation_variance=np.ones_like(base) * 4)
            for arm in ARMS
        }
        return score_all(
            saved, old, historical, pred, np.tile([1.0, 2.0, 3.0, 4.0], (1168, 1))
        )

    def passing_metrics(self):
        m, _, _ = self.sample()
        for name, value in (
            ("M0", 10.0),
            ("v1.1-e0", 10.0),
            ("BPLUS_GP_V1", 9.8),
            ("TIME_ONLY", 10.0),
            ("ADD", 9.0),
        ):
            mask = m.strategy == name
            m.loc[mask, ["mae_mm", "rmse_mm"]] = value
            if name != "M0":
                m.loc[mask, ["crps_mm", "interval_score_90_mm"]] = value
                m.loc[mask, "coverage_90"] = 0.8
                m.loc[mask, "width_90_mm"] = 10.0
        return m

    def test_all_strategy_scores_and_pooled_rmse(self):
        m, a, d = self.sample()
        self.assertEqual((len(m), len(a), len(d)), (40, 20, 22760))
        self.assertTrue(m[m.strategy == "M0"].crps_mm.isna().all())
        rows = a[(a.strategy == "M0") & (a.part == "prediction")].set_index(
            "aggregation"
        )
        self.assertAlmostEqual(rows.loc["point_mean", "rmse_mm"], 2.5)
        self.assertAlmostEqual(rows.loc["pooled", "rmse_mm"], np.sqrt(7.5))
        with tempfile.TemporaryDirectory() as folder:
            f = Path(folder) / "scores.csv"
            m.to_csv(f, index=False)
            compare_frame(m, f, self.spec)
            m.loc[0, "rmse_mm"] += 0.01
            with self.assertRaises(AssertionError):
                compare_frame(m, f, self.spec)

    def test_material_thresholds_and_per_point_failure(self):
        m = self.passing_metrics()
        self.assertTrue(decide(m, self.spec)["effect_passed"])
        mask = (m.strategy == "ADD") & (m.part == "prediction")
        m.loc[mask, ["mae_mm", "rmse_mm"]] = 9.6
        result = decide(m, self.spec)
        self.assertFalse(result["effect_passed"])
        gate = next(
            g
            for g in result["aggregate_gates"]
            if g["name"] == "prediction_rmse_mm_vs_TIME_ONLY"
        )
        self.assertEqual(gate["required_reduction"], 0.5)
        self.assertFalse(gate["passed"])
        m = self.passing_metrics()
        mask = (m.strategy == "ADD") & (m.part == "prediction") & (m.station == "MJ1")
        m.loc[mask, "crps_mm"] = 10.1
        result = decide(m, self.spec)
        self.assertFalse(result["point_guards"][3]["checks"]["crps_mm_vs_v1.1-e0"])
        self.assertFalse(result["effect_passed"])

    def test_probability_threshold_coverage_and_historical_retention(self):
        m = self.passing_metrics()
        mask = (m.strategy == "ADD") & (m.part == "prediction")
        m.loc[mask, "crps_mm"] = 9.95
        result = decide(m, self.spec)
        self.assertFalse(
            next(
                g
                for g in result["aggregate_gates"]
                if g["name"] == "prediction_crps_mm_vs_TIME_ONLY"
            )["passed"]
        )
        self.assertFalse(
            next(
                g
                for g in result["aggregate_gates"]
                if g["name"] == "retain_gp_v1_crps_mm"
            )["passed"]
        )
        m = self.passing_metrics()
        mask = mask & (m.station == "MJ1")
        m.loc[mask, "coverage_90"] = 0.8 - 1 / 376
        self.assertTrue(decide(m, self.spec)["effect_passed"])
        m.loc[mask, "coverage_90"] -= 1 / 376
        self.assertFalse(decide(m, self.spec)["effect_passed"])
        m = self.passing_metrics()
        m.loc[mask, "width_90_mm"] = 11.0
        m.loc[mask, "interval_score_90_mm"] = 10.0
        self.assertFalse(
            decide(m, self.spec)["point_guards"][3]["checks"]["width_vs_v1.1-e0"]
        )
        m = self.passing_metrics()
        m.loc[m.strategy == "M0", "crps_mm"] = 1
        with self.assertRaises(ValueError):
            decide(m, self.spec)

    def test_budget_counts_include_line_search_requests(self):
        spec = copy.deepcopy(self.spec)
        spec["optimizer"]["max_evaluations"] = 1
        count = []

        def obj(x, eval_gradient):
            count.append(1)
            return float(sum(x * x)), 2 * x

        opt = BoundedOptimizer(spec)
        with self.assertRaises(RuntimeError):
            opt(obj, np.array([1.0]), np.array([[-2.0, 2.0]]))
        self.assertEqual(len(count), 1)

    def test_runner_eight_lock_order_and_failure_stop(self):
        import run_ootang_bplus_additive_gp as run

        class SyntheticModel:
            def __init__(self, spec, arm, optimizer):
                self.kernel = kernel(spec, arm)
                self.optimizer = optimizer
                self.log_marginal_likelihood_value_ = 0.0

            def fit(self, x, y):
                self.optimizer.result = {"success": True}
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
        historical = dict(
            mean=np.zeros((1168, 4)), observation_variance=np.ones((1168, 4))
        )
        from physics_guided_gp import raw_features

        for failure in (None, 3):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as folder,
                ExitStack() as stack,
            ):
                spec = copy.deepcopy(self.spec)
                spec["output_dir"] = str(Path(folder) / "run")
                spec["old_gp_dir"] = folder
                for key in ("implementation_deadline_utc", "training_deadline_utc"):
                    spec[key] = (
                        datetime.now(timezone.utc) + timedelta(hours=2)
                    ).isoformat()
                norm = prepare_inputs(
                    raw_features(saved), saved["mean"], np.zeros((792, 4)), spec
                ).normalizers
                (Path(folder) / "normalizers.json").write_text(json.dumps(norm))
                stack.enter_context(
                    patch.object(run, "specification", return_value=spec)
                )
                stack.enter_context(
                    patch.object(
                        run,
                        "snapshot",
                        return_value={"commit": "synthetic", "files": {}},
                    )
                )
                stack.enter_context(
                    patch.object(
                        run, "load_sources", return_value=(saved, old, historical, {})
                    )
                )
                stack.enter_context(
                    patch.object(run, "check_saved_scores", return_value={})
                )
                stack.enter_context(patch("builtins.print"))
                calls = []

                def factory(spec, arm, optimizer):
                    calls.append(arm)
                    if len(calls) == failure:
                        raise RuntimeError("synthetic failure")
                    return SyntheticModel(spec, arm, optimizer)

                stack.enter_context(patch.object(run, "new_gp", side_effect=factory))

                def fake_predict(model, arm, x, base, scale, spec):
                    return dict(mean=base, observation_variance=np.ones(len(x))), {}

                stack.enter_context(
                    patch.object(run, "predict", side_effect=fake_predict)
                )

                def dump(model, path, compress):
                    Path(path).write_bytes(b"synthetic")

                stack.enter_context(patch.object(run.joblib, "dump", side_effect=dump))
                reads = []

                def labels(path, rows):
                    reads.append(rows)
                    if rows == 1168:
                        self.assertEqual(calls, list(ARMS) * 4)
                        self.assertTrue(
                            (Path(spec["output_dir"]) / "prediction_lock.json").exists()
                        )
                    return np.zeros((rows, 4))

                stack.enter_context(
                    patch.object(run, "read_labels", side_effect=labels)
                )
                code = run.main()
                self.assertEqual(code, 0 if failure is None else 1)
                self.assertEqual(reads, [792, 1168] if failure is None else [792])
                with self.assertRaises(FileExistsError):
                    run.main()


if __name__ == "__main__":
    unittest.main()
