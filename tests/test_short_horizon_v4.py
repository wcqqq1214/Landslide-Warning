"""Checks for the seven-endpoint protocol, independent of effectiveness scores."""

import numpy as np
import unittest
import torch

from short_horizon.common import ROOT, load_spec
from short_horizon.data import observations, ridge_features
from short_horizon.evaluation import ErrorCalibration, c16_means
from short_horizon.models import Scaling, new_model, objective, RidgeMean
from short_horizon.physics import PhysicalBank, replay, BatchDay, day_numpy
from rolling_probability.data import Teacher, example
from rolling_probability.scoring import crps, interval


def spec():
    return load_spec(ROOT / "config/ootang_short_horizon_comparison.v4_0.json")


def real(spec):
    y, f, _ = observations(spec, 620)
    bank = PhysicalBank(f, y[0], [252, 342, 432, 612])
    rows = []
    for n in (300, 450, 612):
        q, forcing, b, s, packed, err = bank.forecast(n)
        teacher = Teacher(
            q, b, forcing, s["moisture"], s["rain_head"], s["reservoir_head"]
        )
        x, z, m = example(y[:n], teacher, 7, 30)
        rows.append(
            dict(
                origins=n,
                x=x,
                z=z,
                anchor=m["B_ANCHOR"],
                last_y=y[n - 1],
                drift=y[n - 1] + np.arange(1, 8)[:, None] * (y[n - 1] - y[n - 2]),
                state=packed[n - 1 : n + 7],
                force=s["force"][n : n + 7],
                elastic=s["rain_head"][n : n + 7] * bank.theta[q][[44, 45, 46, 46]],
                coeff=bank.coeff[q],
                target=y[n : n + 7],
                teacher=q,
            )
        )
    data = {k: np.asarray([r[k] for r in rows]) for k in rows[0]}
    data["obs"] = bank.obs
    return bank, data, y, f


def test_date_masks(spec):
    for phase, (start, end) in spec["stages"].items():
        for h in range(1, 8):
            origins = np.arange(start, end - h + 1)
            assert len(origins) == end - start - h + 1
            assert origins[-1] + h - 1 == end - 1


def test_causal_features_and_forcing(real):
    bank, data, y, f = real
    changed = f.copy()
    changed[450:] = [1000, 200]
    original = bank.forecast(450)
    bank.forcing = changed
    perturbed = bank.forecast(450)
    bank.forcing = f
    for a, b in zip(
        (original[1], original[2], original[4]),
        (perturbed[1], perturbed[2], perturbed[4]),
    ):
        np.testing.assert_array_equal(a, b)
    for i, n in enumerate(data["origins"]):
        assert data["teacher"][i] <= n
        assert data["state"][i].shape == (8, 24)
        np.testing.assert_allclose(
            data["x"][i, -1, 0], y[n - 1] - y[n - 2], atol=0, rtol=0
        )


def test_exact_daily_replay(real):
    bank, data, y, f = real
    for i in range(3):
        s = data["state"][i]
        exact, _ = replay(
            s[0], s[1:, 20:], data["force"][i], data["elastic"][i], data["coeff"][i]
        )
        np.testing.assert_allclose(exact, s[1:], atol=1e-8, rtol=0)


def test_daily_old_state_gradient(real):
    _, data, _, _ = real
    i = 1
    old = data["state"][i, 0].copy()
    # Check a directional derivative including all physical memory fields.
    direction = np.random.default_rng(7).normal(size=24)
    direction[20:] = 0
    end = data["state"][i, 1, 20:]
    f = data["force"][i, 0]
    e = data["elastic"][i, 0]
    c = data["coeff"][i]
    ts = [torch.tensor(a[None], dtype=torch.float64) for a in (old, end, f, e, c)]
    ts[0].requires_grad_(True)
    weight = torch.tensor(
        np.random.default_rng(8).normal(size=(1, 24)), dtype=torch.float64
    )
    (BatchDay.apply(*ts) * weight).sum().backward()
    analytic = float(ts[0].grad.numpy()[0] @ direction)
    eps = 1e-4
    high = day_numpy(old + eps * direction, end, f, e, c)[0]
    low = day_numpy(old - eps * direction, end, f, e, c)[0]
    numeric = float(np.sum((high - low) / (2 * eps) * weight.numpy()[0]))
    np.testing.assert_allclose(analytic, numeric, rtol=2e-5, atol=2e-6)


def test_zero_correction_recovers_anchor(real, spec, name):
    _, data, _, _ = real
    scale = Scaling(data)
    model = new_model(name, spec)
    mean, _ = model(scale.tensors(data), scale, name)
    np.testing.assert_allclose(mean.detach().numpy(), data["anchor"], atol=1e-8, rtol=0)


def test_pinn_loss_and_gradient_finite(real, spec):
    _, data, _, _ = real
    scale = Scaling(data)
    model = new_model("PINN_EQ", spec)
    loss, parts = objective(model, "PINN_EQ", scale.tensors(data), scale, spec)
    loss.backward()
    assert np.isfinite(float(loss.detach()))
    assert parts["equation"] < 1e-12
    assert all(
        p.grad is None or torch.isfinite(p.grad).all() for p in model.parameters()
    )


def test_ridge_features_and_exact_regularized_solution(real):
    _, data, y, _ = real
    x = ridge_features(data)
    for i, n in enumerate(data["origins"]):
        np.testing.assert_allclose(
            x[i, :, 0, 1], (y[n - 1, 0] - y[n - 4, 0]) / 3, atol=1e-12
        )
    model = RidgeMean.fit(data, Scaling(data), 0.1, "RR_BRES")
    z = (x - model.feature_mean) / model.feature_std
    A = np.column_stack([np.ones(len(z)), z[:, 0, 0]])
    penalty = np.diag([0] + [0.1] * 16)
    yy = (data["target"][:, 0, 0] - data["anchor"][:, 0, 0]) / model.target_unit[0, 0]
    expected = np.linalg.lstsq(
        np.vstack([A, np.sqrt(len(A)) * np.sqrt(penalty)]),
        np.r_[yy, np.zeros(17)],
        rcond=None,
    )[0]
    np.testing.assert_allclose(model.beta[0, 0], expected, atol=1e-10, rtol=0)
    assert np.isfinite(model.predict(data)).all()


def test_scale_initialization_and_delayed_updates():
    t = np.arange(120, dtype=float)
    labels = np.repeat((t * t)[:, None], 4, axis=1)
    cal = ErrorCalibration(labels[:100], 100)
    expected = np.arange(1, 8) * (np.arange(1, 8) + 1)
    np.testing.assert_allclose(cal.scale(100), np.repeat(expected[:, None], 4, axis=1))
    before = cal.scale(100).copy()
    with unittest.TestCase().assertRaises(ValueError):
        cal.update(6, np.ones(4), 100, 100, 101)
    with unittest.TestCase().assertRaises(ValueError):
        cal.update(0, np.ones(4), 100, 100, 100)
    cal.update(0, np.zeros(4), 100, 100, 101)
    after = cal.scale(101)
    np.testing.assert_allclose(after[0], before[0] * np.sqrt(89 / 90))
    np.testing.assert_array_equal(after[1:], before[1:])
    with unittest.TestCase().assertRaises(ValueError):
        cal.update(0, np.zeros(4), 100, 100, 101)


def test_previous_predictions_seed_scale_without_fitted_errors():
    labels = np.zeros((200, 4))
    origins = np.arange(100, 200)
    means = np.full((100, 7, 4), 3.0)
    for i, n in enumerate(origins):
        means[i, n + np.arange(7) >= 200] = np.nan
    cal = ErrorCalibration(labels, 200, dict(origins=origins, mean=means))
    np.testing.assert_allclose(cal.scale(200), 3.0)
    assert all(
        s["previous_model_count"] == 90 and s["drift_count"] == 0 for s in cal.sources
    )


def test_frozen_c16_core_replay(spec):
    # The PHYS fixture values do not affect the independent CORE branch.
    origins = np.arange(252, 1461)
    anchor = np.zeros((len(origins), 7, 4))
    cache = dict(origins=origins, anchor=anchor)
    for phase in ("development", "later_exploratory"):
        _, audit = c16_means(spec, phase, cache)
        assert audit["core_replay_max_difference_mm"] <= 1e-8


class ShortHorizonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)
        cls.settings = spec()
        cls.actual = real(cls.settings)

    def test_dates(self):
        test_date_masks(self.settings)

    def test_causality(self):
        test_causal_features_and_forcing(self.actual)

    def test_physics_replay(self):
        test_exact_daily_replay(self.actual)

    def test_gradient(self):
        test_daily_old_state_gradient(self.actual)

    def test_zero_residual(self):
        for name in ("CL_BRES", "PINN_EQ", "PINN_NOEQ"):
            with self.subTest(model=name):
                test_zero_correction_recovers_anchor(self.actual, self.settings, name)

    def test_equation_loss(self):
        test_pinn_loss_and_gradient_finite(self.actual, self.settings)

    def test_ridge(self):
        test_ridge_features_and_exact_regularized_solution(self.actual)

    def test_maturity(self):
        test_scale_initialization_and_delayed_updates()

    def test_previous_scale(self):
        test_previous_predictions_seed_scale_without_fitted_errors()

    def test_c16(self):
        test_frozen_c16_core_replay(self.settings)

    def test_probability_analytic(self):
        y = np.zeros(4)
        sigma = np.ones(4)
        np.testing.assert_allclose(
            crps(y, y, sigma), (np.sqrt(2) - 1) / np.sqrt(np.pi), atol=1e-14
        )
        coverage, width, score = interval(y, y, sigma, 0.9)
        assert coverage.all()
        np.testing.assert_allclose(width, 3.289707253902945, atol=1e-14)
        np.testing.assert_array_equal(width, score)


if __name__ == "__main__":
    unittest.main()
