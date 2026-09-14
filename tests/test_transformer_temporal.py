"""Research-critical boundary tests; no experimental optimizer runs."""

import numpy as np
import pandas as pd
import unittest
import tempfile
from pathlib import Path
import torch

from transformer_temporal.core import (
    ALPHAS,
    ARM,
    TrajectoryModel,
    objective,
    predict,
    read_labels,
    select_and_calibrate,
    shrink,
    slice_roles,
    spec,
    training_inputs,
)


def test_future_labels_are_not_read(tmp_path):
    p = tmp_path / "data.csv"
    frame = pd.DataFrame(
        {k + "/mm": np.arange(40, dtype=float) for k in spec()["points"]}
    )
    frame.to_csv(p, index=False)
    expected = read_labels(p, 20)
    frame.iloc[20:] = np.nan
    frame.to_csv(p, index=False)
    np.testing.assert_array_equal(read_labels(p, 20), expected)


def test_loss_and_analytic_gradient(strength):
    q = torch.tensor(
        np.arange(40).reshape(10, 4) / 40, dtype=torch.float64, requires_grad=True
    )
    r = torch.cos(q.detach())
    total, _, _ = objective(q, r, strength)
    expected = (1 + strength) * (q - r / (1 + strength)).square().mean()
    expected += strength / (1 + strength) * r.square().mean()
    torch.testing.assert_close(total, expected, atol=1e-14, rtol=0)
    total.backward()
    torch.testing.assert_close(
        q.grad, 2 * ((1 + strength) * q.detach() - r) / q.numel(), atol=1e-14, rtol=0
    )


def test_alpha_endpoints_and_seed_pairing():
    rng = np.random.default_rng(2)
    physical, neural = rng.normal(size=(30, 4)), rng.normal(size=(3, 30, 4))
    np.testing.assert_array_equal(shrink(physical, neural[0], 0), physical)
    for weight in ALPHAS.values():
        actual = np.stack([shrink(physical, a, weight) for a in neural]).mean(0)
        np.testing.assert_allclose(
            actual, shrink(physical, neural.mean(0), weight), atol=1e-14
        )


def test_selection_calibration_and_maturity():
    cfg = spec()
    y = np.zeros((400, 4))
    a = np.zeros((500, 4))
    b = np.ones((500, 4))
    a[190:280] = 9
    record, sigmas, errors = select_and_calibrate(
        {"A0": a, "A1": b}, y, 100, 400, ["A0", "A1"], cfg
    )
    assert record["selected"] == "A0"
    np.testing.assert_array_equal(sigmas["A0"], np.full(4, 9))
    np.testing.assert_array_equal(errors["A0"], np.full((90, 4), 9))
    y[280:] = 1e9
    updated, _, _ = select_and_calibrate(
        {"A0": a, "A1": b}, y, 100, 400, ["A0", "A1"], cfg
    )
    assert updated == record
    with unittest.TestCase().assertRaises(ValueError):
        slice_roles(100, 279)
    with unittest.TestCase().assertRaises(ValueError):
        select_and_calibrate({"A0": a}, y, 100, 399, ["A0"], cfg)


def test_scaling_causality_and_zero_baseline():
    cfg = spec()
    torch.set_num_threads(1)
    rng = np.random.default_rng(4)
    data = dict(x=rng.normal(size=(60, 22)), mean=rng.normal(size=(60, 4)))
    y = rng.normal(size=(30, 4))
    scale, _, target = training_inputs(data, y, cfg)
    assert scale.state["training_rows"] == 30 and target.shape == (30, 4)
    model = TrajectoryModel(cfg, 0, ARM)
    np.testing.assert_array_equal(
        predict(model, scale, ARM, data["x"], data["mean"]), data["mean"]
    )
    with torch.no_grad():
        model.head.weight.fill_(0.012)
    before = predict(model, scale, ARM, data["x"], data["mean"])
    data["x"][40:] += 100
    after = predict(model, scale, ARM, data["x"], data["mean"])
    np.testing.assert_allclose(before[:40], after[:40], atol=1e-10, rtol=0)


def test_selection_tie_prefers_smaller_alpha():
    cfg = spec()
    candidates = {k: np.zeros((400, 4)) for k in reversed(ALPHAS)}
    record, _, _ = select_and_calibrate(
        candidates, np.zeros((300, 4)), 100, 300, cfg["selection"]["alpha_order"], cfg
    )
    assert record["selected"] == "A0"


class TemporalContracts(unittest.TestCase):
    def test_label_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            test_future_labels_are_not_read(Path(temp))

    def test_losses(self):
        for strength in [0, 1 / 3, 1, 3]:
            with self.subTest(strength=strength):
                test_loss_and_analytic_gradient(strength)

    def test_alpha(self):
        test_alpha_endpoints_and_seed_pairing()

    def test_selection(self):
        test_selection_calibration_and_maturity()

    def test_causality(self):
        test_scaling_causality_and_zero_baseline()

    def test_ties(self):
        test_selection_tie_prefers_smaller_alpha()


if __name__ == "__main__":
    unittest.main()
