"""Numerical gates for frozen and prefix physics; no validation labels choose a model."""

import numpy as np
import torch
from scipy.integrate import quad
from scipy.special import ndtr
from scipy.optimize import nnls
from .data import phase_masks, COUNTS
from .features import fit_scalers
from .mechanics import Mechanics, tensor
from .models import M1, M2
from .probability import crps, summarize
from .reference import save_json
from .training import setup


def validate_physics(ref, theta, drivers, labels, output, name):
    setup(31415)
    n = 792
    d = drivers.prefix(n)
    mechanics = Mechanics(ref, theta, d)
    scalers = fit_scalers(d, mechanics, n)
    raw, branches, audit = mechanics.zero_trajectory()
    s = mechanics.reference
    errors = {
        k: float(np.max(abs(value - s[k])))
        for k, value in {
            "coordinates": raw[:, :4] + raw[:, 20:],
            "plastic": raw[:, 4:8],
            "background": raw[:, 20:],
            "basal_reaction": raw[:, 8:12],
            "contact": raw[:, 12:16],
            "bulk_reaction": raw[:, 16:20],
        }.items()
    }
    if max(errors[k] for k in ["coordinates", "plastic", "background"]) > 1e-5:
        raise ArithmeticError("M2 zero correction differs from original B+")
    m1, m2 = M1(), M2()
    with torch.no_grad():
        first = m1.predict(d, mechanics, scalers[0]).numpy()
        second = m2.predict(d, mechanics, scalers[1]).numpy()
    m1_error = float(np.max(abs(first[29:] - (mechanics.u + d.y0)[29:])))
    m2_error = float(np.max(abs(second - (mechanics.u + d.y0))))
    if m1_error > 1e-8 or m2_error > 1e-5:
        raise ArithmeticError("Zero network output gate failed")
    # Fixed nonzero initialization exercises the hidden layers, not just the output layer.
    generator = torch.Generator().manual_seed(31415)
    with torch.no_grad():
        for p in m2.parameters():
            p.copy_(0.01 * torch.randn(p.shape, generator=generator, dtype=p.dtype))
    params = list(m2.parameters())
    base = torch.nn.utils.parameters_to_vector(params).detach().clone()
    mu, state, factors, checks = m2.predict(
        d, mechanics, scalers[1], return_states=True
    )
    loss = ((mu[30:] - tensor(labels[:n][30:])) / 100).square().mean()
    grads = torch.autograd.grad(loss, params)
    gradient = torch.cat([g.reshape(-1) for g in grads])
    if any(float(g.norm()) == 0 for g in grads):
        raise ArithmeticError("Vacuous hidden-layer gradient test")
    directions = []
    for direction_id in range(4):
        v = torch.randn(base.shape, generator=generator, dtype=torch.float64)
        v = v / v.norm()
        ad = float(gradient @ v)
        differences = []
        for h in [1e-4, 1e-5, 1e-6]:
            values = []
            for sign in [1, -1]:
                torch.nn.utils.vector_to_parameters(base + sign * h * v, params)
                with torch.no_grad():
                    pred = m2.predict(d, mechanics, scalers[1])
                    values.append(
                        float(
                            ((pred[30:] - tensor(labels[:n][30:])) / 100)
                            .square()
                            .mean()
                        )
                    )
            fd = (values[0] - values[1]) / (2 * h)
            tolerance = 1e-5 + 1e-3 * max(abs(ad), abs(fd))
            differences.append(
                dict(
                    h=h,
                    ad=ad,
                    fd=fd,
                    error=abs(ad - fd),
                    tolerance=tolerance,
                    passed=abs(ad - fd) <= tolerance,
                )
            )
        stable = any(
            differences[i]["passed"]
            and differences[i + 1]["passed"]
            and abs(differences[i]["fd"] - differences[i + 1]["fd"])
            <= max(differences[i]["tolerance"], differences[i + 1]["tolerance"])
            for i in range(2)
        )
        if not stable:
            raise ArithmeticError(
                f"Full 792-day gradient direction {direction_id} failed: {differences}"
            )
        directions.append(
            dict(direction=direction_id, comparisons=differences, adjacent_stable=True)
        )
    torch.nn.utils.vector_to_parameters(base, params)
    short = d.prefix(120)
    short_mechanics = Mechanics(ref, theta, short)
    with torch.no_grad():
        short_pred = m2.predict(short, short_mechanics, scalers[1]).numpy()
        long_pred = m2.predict(d, mechanics, scalers[1]).numpy()
    prefix_error = float(np.max(abs(short_pred - long_pred[:120])))
    if prefix_error > 1e-8:
        raise ArithmeticError("M2 prefix prediction depends on future drivers")
    if audit[:, 4].sum() == 0 or float(checks[:, 4].sum()) == 0:
        raise ArithmeticError(
            "Gradient trajectory does not exercise active-set changes"
        )
    result = dict(
        status="passed",
        physics=name,
        zero_state_errors=errors,
        m1_zero_max_mm=m1_error,
        m2_zero_max_mm=m2_error,
        substeps_checked=64 * (n - 1),
        min_x=float(checks[:, 0].min()),
        min_w=float(checks[:, 1].min()),
        max_normalized_complementarity=float(checks[:, 2].max()),
        min_slip_tolerance_margin=float(checks[:, 3].min()),
        active_set_changes=int(checks[:, 4].sum()),
        full_history_days=n,
        gradient_seed=31415,
        nonzero_weight_std=0.01,
        gradient_layer_norms=[float(g.norm()) for g in grads],
        directions=directions,
        prefix_max_abs_mm=prefix_error,
        multiplier_range=[float(factors.min().detach()), float(factors.max().detach())],
    )
    save_json(output / f"validation_{name}.json", result)
    return result


def validate_probability():
    mu = np.array([-3.0, 1.0, 5.0])
    sigma = np.array([0.4, 2.0, 0.7])
    y = 2.3
    analytic = float(crps(mu, sigma, y))

    def cdf(x):
        return float(ndtr((x - mu) / sigma).mean())

    integral = (
        quad(lambda x: cdf(x) ** 2, -np.inf, y, epsabs=1e-10)[0]
        + quad(lambda x: (1 - cdf(x)) ** 2, y, np.inf, epsabs=1e-10)[0]
    )
    if abs(analytic - integral) > 1e-8:
        raise ArithmeticError("Mixture CRPS differs from numerical CDF integration")
    q = summarize(mu, sigma)
    values = [
        q[k]
        for k in [
            "lower_95",
            "lower_90",
            "lower_80",
            "upper_80",
            "upper_90",
            "upper_95",
        ]
    ]
    if not np.all(np.diff(values) > 0):
        raise ArithmeticError("Non-nested probability intervals")
    return dict(
        analytic_crps=analytic,
        numerical_crps=integral,
        absolute_error=abs(analytic - integral),
    )


def validate_switch(mechanics):
    """Check the LCP itself against NNLS and each side of an exact zero-force switch."""
    a = mechanics.coeff[46:62].reshape(4, 4)
    maps = mechanics.coeff[62:].reshape(16, 4, 4)
    upper = np.linalg.cholesky(a).T
    direction = np.array([1.0, -0.3, 0.2, -0.1])

    def solve(rhs):
        for mask in range(16):
            x = maps[mask] @ rhs
            w = a @ x - rhs
            if np.min(x) >= -1e-14 and np.min(w) >= -1e-12:
                return x, maps[mask], mask
        raise ArithmeticError("No switch fixture branch")

    rows = []
    for sign in [-1, 1]:
        rhs = sign * 1e-3 * direction
        x, matrix, mask = solve(rhs)
        independent = nnls(upper, np.linalg.solve(upper.T, rhs))[0]
        np.testing.assert_allclose(x, independent, atol=1e-10, rtol=1e-8)
        ad = matrix @ direction
        h = 1e-6
        fd = (solve(rhs + h * direction)[0] - solve(rhs - h * direction)[0]) / (2 * h)
        np.testing.assert_allclose(ad, fd, atol=1e-8, rtol=1e-6)
        rows.append(dict(side=sign, mask=mask, derivative=ad.tolist()))
    continuity = [
        float(np.max(abs(solve(h * direction)[0] - solve(-h * direction)[0])))
        for h in [1e-3, 1e-5, 1e-7]
    ]
    if not continuity[2] < continuity[1] < continuity[0]:
        raise ArithmeticError("Switch continuity failed")
    return dict(
        sides=rows,
        continuity_gaps=continuity,
        unique_derivative_at_switch_required=False,
    )


def validate_dates(drivers):
    values = {}
    for stage, (fit, end) in COUNTS.items():
        masks = phase_masks(drivers.dates[:end], stage)
        values[stage] = {k: int(v.sum()) for k, v in masks.items()}
    if values != {
        "development": {"train": 762, "prediction": 376},
        "final": {"train": 1138, "prediction": 293},
    }:
        raise ValueError("Invalid common denominators")
    return values
