import copy
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
import pandas as pd
import torch
from scipy.special import ndtri

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from physics_guided.reference import ROOT, load, prepare, frozen_theta
from physics_guided.data import read_data, training_labels, phase_masks
from physics_guided.calibration import initial
from physics_guided.mechanics import Mechanics, Day, tensor
from physics_guided.features import fit_scalers, point_features
from physics_guided.models import M1, M2, ScaleHead
from physics_guided.probability import quantile, crps, summarize
from physics_guided.validation import (
    validate_probability,
    validate_dates,
    validate_switch,
)
from physics_guided.training import setup, choose, rank_routes
from physics_guided.reporting import export_stage, acceptance


class PhysicsGuidedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup(0)
        path = ROOT / "runtime/ootang_bplus_v1_1/reference"
        cls.ref = load(path) if path.exists() else prepare(path)
        cls.drivers, cls.labels = read_data(ROOT / "data/monitoring_data.csv")
        cls.theta, _ = frozen_theta(cls.ref)
        cls.short = cls.drivers.prefix(45)
        cls.mechanics = Mechanics(cls.ref, cls.theta, cls.short)
        cls.scalers = fit_scalers(cls.short, cls.mechanics, 40)

    def test_data_invalid_dates_and_nonfinite(self):
        frame = pd.read_csv(ROOT / "data/monitoring_data.csv")
        for change in ["gap", "duplicate", "nan"]:
            d = frame.copy()
            if change == "gap":
                d = d.drop(index=3)
            elif change == "duplicate":
                d.loc[3, "Date"] = d.loc[2, "Date"]
            else:
                d.loc[3, "Rainfall/mm"] = np.nan
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "input.csv"
                d.to_csv(path, index=False)
                with self.assertRaises(ValueError):
                    read_data(path)

    def test_masks_and_label_isolation(self):
        validate_dates(self.drivers)
        changed = self.labels.copy()
        changed[792:] += 1e6
        np.testing.assert_array_equal(
            training_labels(self.labels, "development"),
            training_labels(changed, "development"),
        )
        changed = self.labels.copy()
        changed[1168:] -= 1e6
        np.testing.assert_array_equal(
            training_labels(self.labels, "final"), training_labels(changed, "final")
        )
        self.assertEqual(
            phase_masks(self.drivers.dates[:1168], "development")["train"].sum(), 762
        )

    def test_initials_are_clean_and_use_linear_creep(self):
        for start, dry, wet in [("A", 0.02, 0.1), ("B", 0.05, 0.2)]:
            x = initial(self.ref, start)
            self.assertEqual(len(x), 54)
            self.assertTrue(np.all(x >= self.ref.LO) and np.all(x <= self.ref.HI))
            np.testing.assert_array_equal(x[24:28], dry)
            np.testing.assert_array_equal(x[47:51], wet)
            np.testing.assert_array_equal(x[28:32], 0.0)

    def test_feature_mapping_and_training_only_scaler(self):
        x = point_features(self.short, self.mechanics)
        self.assertEqual(x.shape, (45, 20, 4))
        np.testing.assert_allclose(x[:, 8:12, 0], self.mechanics.reference["rain_head"])
        np.testing.assert_array_equal(x[:, 8:16, 0], x[:, 8:16, 3])
        np.testing.assert_allclose(
            x[:, 0], self.mechanics.reference["coordinates"] @ self.mechanics.ctx.obs.T
        )
        old = copy.copy(self.mechanics)
        old.u = self.mechanics.u.copy()
        old.u[40:] += 9999
        altered = fit_scalers(self.short, old, 40)
        for a, b in zip(altered, self.scalers):
            np.testing.assert_array_equal(a.mean, b.mean)
            np.testing.assert_array_equal(a.scale, b.scale)

    def test_m1_zero_warmup_and_prefix(self):
        model = M1()
        with torch.no_grad():
            a = model.predict(
                self.short, self.mechanics, self.scalers[0], chunk=3
            ).numpy()
            b = model.predict(
                self.short, self.mechanics, self.scalers[0], chunk=128
            ).numpy()
        self.assertTrue(np.isnan(a[:29]).all())
        np.testing.assert_allclose(
            a[29:], self.mechanics.u[29:] + self.short.y0, atol=1e-12
        )
        np.testing.assert_allclose(a, b, atol=1e-12, equal_nan=True)

    def test_m1_chunk_gradient_equivalence(self):
        model = M1()
        with torch.no_grad():
            model.output.weight.fill_(0.01)
        x = tensor(
            self.scalers[0].transform(point_features(self.short, self.mechanics))
        )
        windows = torch.stack([x[t - 29 : t + 1, :, None] for t in range(30, 45)])
        target = tensor(np.arange(60).reshape(15, 4) / 100)
        model.zero_grad()
        ((model(windows) - target) ** 2).mean().backward()
        full = torch.cat([p.grad.flatten() for p in model.parameters()]).clone()
        model.zero_grad()
        for t in range(0, 15, 4):
            ((model(windows[t : t + 4]) - target[t : t + 4]) ** 2).sum().div(
                60
            ).backward()
        chunked = torch.cat([p.grad.flatten() for p in model.parameters()])
        torch.testing.assert_close(full, chunked, atol=1e-10, rtol=1e-10)

    def test_day_backward_matches_unrolled_multiday_torch(self):
        solver = self.mechanics
        coeff = tensor(solver.coeff)
        beta, ar, kr, ac, ae = coeff[:4], coeff[4:8], coeff[8:12], coeff[12], coeff[13]
        kc, ke = coeff[14:30].reshape(4, 4), coeff[30:46].reshape(4, 4)
        maps = coeff[62:].reshape(16, 4, 4)
        initial_state = torch.zeros(24, dtype=torch.float64, requires_grad=True)
        ends = tensor(solver.reference["background"][1:5]).clone().requires_grad_()
        old_custom, old_torch = initial_state, initial_state
        previous = 0
        for t in range(1, 5):
            _, masks, _ = solver.numpy_day(
                old_custom.detach().numpy(), ends[t - 1].detach().numpy(), t, previous
            )
            old_custom, branch, _ = Day.apply(
                old_custom, ends[t - 1], solver, t, previous
            )
            previous = int(branch)
            begin = old_torch[20:]
            for step, mask in enumerate(masks):
                z, p, rb, rc, re, bg = old_torch.split(4)
                newbg = begin + (step + 1) / 64 * (ends[t - 1] - begin)
                elastic, force = tensor(solver.elastic[t]), tensor(solver.force[t])
                k = beta * (p + elastic - z) + newbg - bg
                rhs = force - ar * rb - ac * rc - ae * re - (ac * kc + ae * ke) @ k
                dx = maps[mask] @ rhs
                pnew = p + dx / beta
                znew = z + beta * (pnew + elastic - z)
                old_torch = torch.cat(
                    [
                        znew,
                        pnew,
                        ar * (rb + kr * dx / beta),
                        ac * (rc + kc @ (k + dx)),
                        ae * (re + ke @ (k + dx)),
                        newbg,
                    ]
                )
        weights = torch.arange(1, 25, dtype=torch.float64) / 24
        g1 = torch.autograd.grad(
            (old_custom * weights).sum(), (initial_state, ends), retain_graph=True
        )
        g2 = torch.autograd.grad((old_torch * weights).sum(), (initial_state, ends))
        torch.testing.assert_close(old_custom, old_torch, atol=1e-10, rtol=1e-10)
        for a, b in zip(g1, g2):
            torch.testing.assert_close(a, b, atol=1e-9, rtol=1e-9)

    def test_m2_zero_and_switch(self):
        state, _, audit = self.mechanics.zero_trajectory()
        np.testing.assert_allclose(
            state[:, :4] + state[:, 20:],
            self.mechanics.reference["coordinates"],
            atol=1e-10,
        )
        self.assertGreaterEqual(audit[:, 0].min(), -1e-8)
        validate_switch(self.mechanics)
        with torch.no_grad():
            mu = M2().predict(self.short, self.mechanics, self.scalers[1]).numpy()
        np.testing.assert_allclose(mu, self.mechanics.u + self.short.y0, atol=1e-10)

    def test_probability_against_independent_integral(self):
        validate_probability()
        mu = np.ones((3, 2)) * 10
        sigma = np.ones((3, 2)) * 2
        np.testing.assert_allclose(
            quantile(mu, sigma, 0.95), 10 + 2 * ndtri(0.95), atol=1e-6
        )
        np.testing.assert_allclose(
            crps(mu, sigma, np.array([10.0, 10.0])),
            2 * (np.sqrt(2) - 1) / np.sqrt(np.pi),
            atol=1e-12,
        )
        np.testing.assert_allclose(summarize(mu + 1e9, sigma)["std"], 2.0, atol=1e-12)
        with self.assertRaises(ValueError):
            summarize(mu[:2], sigma[:2])

    def test_scale_initialization(self):
        for rmse in [0.0, 0.001, 5.0, 100.0]:
            scale = ScaleHead(rmse)(torch.zeros((3, 4, 20), dtype=torch.float64))
            torch.testing.assert_close(scale, torch.full_like(scale, max(rmse, 0.002)))

    def test_common_checkpoint_and_route_ties(self):
        self.assertEqual(choose({0: 2.0, 10: 1.0, 20: 1.0 + 5e-13}), 10)
        a = dict(
            status="complete",
            e_mu=10,
            e_sigma=20,
            mean_candidate_rmse_mm={10: 10.0},
            scale_candidate_crps_mm={20: 5.0},
            parameter_count=100,
        )
        b = copy.deepcopy(a)
        b["mean_candidate_rmse_mm"] = {10: 10.05}
        b["scale_candidate_crps_mm"] = {20: 4.0}
        self.assertEqual(rank_routes({"M1": a, "M2": b}, 9.0)["candidate"], "M2")
        self.assertEqual(
            rank_routes({"M1": a, "M2": b}, 9.0)["retained_baseline"], "M0"
        )

    def test_m0_export_is_na_and_preserves_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            predictions, metrics = export_stage(
                "development",
                self.drivers.prefix(1168),
                self.labels[:1168],
                {},
                self.labels[:1168],
                Path(directory),
            )
        self.assertEqual(len(predictions), 1168 * 4)
        self.assertTrue(predictions.std_mm.isna().all())
        probability = metrics[
            metrics.metric.str.startswith(("crps", "picp", "width", "interval_score"))
        ]
        self.assertTrue(probability.value.isna().all())
        self.assertTrue((probability.status == "not_applicable").all())
        self.assertEqual((predictions.status == "warmup").sum(), 120)

    def test_failed_route_is_preserved_and_acceptance_is_json_safe(self):
        import json

        with tempfile.TemporaryDirectory() as directory:
            predictions, metrics = export_stage(
                "development",
                self.drivers.prefix(1168),
                self.labels[:1168],
                {},
                self.labels[:1168],
                Path(directory),
                failed_routes=["M1"],
            )
        failed = predictions[predictions.model == "M1"]
        self.assertEqual(len(failed), 1168 * 4 * 4)
        self.assertEqual(
            set(failed.component), {"seed_0", "seed_1", "seed_2", "mixture"}
        )
        self.assertTrue((failed.status == "failed").all())
        result = acceptance(metrics)
        self.assertEqual(result["M1"]["evaluation_status"], "not_evaluated")
        json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
