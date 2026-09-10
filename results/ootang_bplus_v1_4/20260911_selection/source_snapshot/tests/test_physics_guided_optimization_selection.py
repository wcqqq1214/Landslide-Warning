"""Leakage, budget, retention and chronological-selection contracts for v1.4."""

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from physics_guided.reference import ROOT, sha
from physics_guided.data import Drivers
from physics_guided_diagnostics.core import (
    array_sha,
    metric_rows,
    validate_training as old_validate,
)
from physics_guided_increment.objective import residual
from physics_guided_optimization_selection.core import (
    read_input,
    validate_training,
    validate_source,
    choose_anchor,
    retain_incumbent,
    fit_stage,
    inner_scores,
    select_recipe,
)
from physics_guided_optimization_selection.run import CONFIG, CONFIG_SHA, PLAN_SHA, seal
from physics_guided_optimization_selection.report import compare


class Reference:
    LO = np.full(54, -1.0)
    HI = np.ones(54)

    def Context(self, forcing):
        return forcing

    def forward(self, theta, context):
        return np.broadcast_to(
            np.arange(len(context))[:, None] * theta[0], (len(context), 4)
        )


def fixture(n):
    y = np.zeros((n, 4))
    f = np.column_stack((np.zeros(n), np.full(n, 150.0)))
    return Drivers(pd.date_range("2016-07-01", periods=n), f, y[0].copy()), y


class OptimizationSelectionTests(unittest.TestCase):
    def test_frozen_config_dates_and_budget(self):
        self.assertEqual(sha(CONFIG), CONFIG_SHA)
        cfg = json.loads(CONFIG.read_text())
        self.assertEqual(sha(ROOT / cfg["plan"]), PLAN_SHA)
        self.assertEqual(
            2 * 800 + 4 * sum(cfg["temporal_selection"]["stage_nfev"]), 14800
        )
        self.assertEqual(cfg["max_optimization_forward_calls"], 56 * 14800)
        for n, inners in cfg["temporal_selection"][
            "inner_training_days_by_outer"
        ].items():
            self.assertTrue(all(k + 180 <= int(n) for k in inners))
        dates = pd.date_range("2016-07-01", periods=792)
        self.assertEqual(
            [str(dates[n - 1].date()) for n in (252, 342, 432, 612, 792)],
            ["2017-03-09", "2017-06-07", "2017-09-05", "2018-03-04", "2018-08-31"],
        )
        self.assertEqual(
            len(set(range(252, 432)) | set(range(342, 522)) | set(range(432, 612))), 360
        )

    def test_reader_does_not_parse_future_rows(self):
        frame = pd.read_csv(
            ROOT / "results/ootang_bplus_v1_2/20260910_diagnostics/input_prefix_792.csv"
        )
        for n in (252, 342, 432, 612):
            with self.subTest(n=n), tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / "input.csv"
                frame.iloc[:n].to_csv(p, index=False)
                with p.open("a") as handle:
                    handle.write("malformed,future,rows,not,allowed,to,parse,extra\n")
                d, y = read_input(p, n)
                validate_training(d, y, n)
                self.assertEqual(len(y), n)
        with self.assertRaises(ValueError):
            read_input(p, 793)

    def test_training_boundary_and_original_validator_unchanged(self):
        d, y = fixture(432)
        validate_training(d, y, 432)
        with self.assertRaises(ValueError):
            validate_training(d, np.zeros((612, 4)), 432)
        short, sy = fixture(252)
        validate_training(short, sy, 252)
        with self.assertRaises(ValueError):
            old_validate(short, sy, 252)
        with self.assertRaises(ValueError):
            validate_training(d, y, 792)

    def test_same_prefix_source_rejects_longer_fit_or_changed_data(self):
        d, y = fixture(432)
        record = dict(
            fit_days=432,
            training_label_sha256=array_sha(y),
            training_forcing_sha256=array_sha(d.forcing),
            theta=np.zeros(54).tolist(),
        )
        validate_source(record, d, y, 432, Reference())
        for changed in [
            dict(record, fit_days=612),
            dict(record, training_label_sha256="wrong"),
        ]:
            with self.assertRaises(ValueError):
                validate_source(changed, d, y, 432, Reference())
        record["theta"][0] = float("nan")
        with self.assertRaises(ValueError):
            validate_source(record, d, y, 432, Reference())

    def test_anchor_order_and_retention_failures_ties(self):
        pool = {
            "first": dict(valid=True, objective=10.0),
            "second": dict(valid=True, objective=10.0 - 5e-13),
            "invalid": dict(valid=False, objective=0.0),
        }
        self.assertEqual(choose_anchor(pool), "first")
        for attempt in [
            None,
            dict(valid=False),
            dict(valid=True, objective=11.0),
            dict(valid=True, objective=10.0 - 5e-8),
            dict(valid=True, objective=float("nan")),
        ]:
            self.assertEqual(retain_incumbent(pool["first"], attempt)[0], "anchor")
        self.assertEqual(
            retain_incumbent(pool["first"], dict(valid=True, objective=9.0))[0],
            "attempt",
        )

    def test_optimizer_preserves_equations_clipping_and_counts(self):
        for task, n, stage in [("A", 432, 1), ("B", 252, 1), ("B", 342, 4)]:
            d, y = fixture(n)
            x = np.zeros(54)
            x[0] = -1
            original = x.copy()

            def optimize(fun, start, **kw):
                self.assertEqual(kw["method"], "trf")
                self.assertEqual(
                    (kw["ftol"], kw["xtol"], kw["gtol"]), (1e-9, 1e-10, 1e-7)
                )
                self.assertEqual(
                    kw["max_nfev"], 900 if task == "B" and stage == 1 else 800
                )
                np.testing.assert_array_equal(start, np.clip(x, -1 + 1e-9, 1 - 1e-9))
                r = fun(start)
                kw["jac"](start)
                weight = 0 if task == "B" and stage == 1 else 100 * n
                expected = residual(
                    Reference().forward(start, d.forcing),
                    y,
                    weight,
                    1.0 if task == "A" else 0.0,
                )
                np.testing.assert_array_equal(r, expected)
                return SimpleNamespace(
                    x=start,
                    cost=float(np.sum(r * r) / 2),
                    nfev=1,
                    njev=1,
                    success=False,
                    status=0,
                    message="fixture",
                    optimality=1.0,
                )

            with patch(
                "physics_guided_optimization_selection.core.least_squares",
                side_effect=optimize,
            ):
                r = fit_stage(Reference(), d, y, n, x, task, stage)
            np.testing.assert_array_equal(x, original)
            np.testing.assert_array_equal(r["input_theta"], original)
            self.assertEqual(r["optimization_forward_calls"], 56)
            self.assertEqual(
                (r["precheck_forward_calls"], r["postcheck_forward_calls"]), (2, 1)
            )
            self.assertNotEqual(r["input_objective"], r["optimizer_initial_objective"])

    def test_optimizer_budget_guard_and_failed_stage_counters(self):
        d, y = fixture(432)

        def exceed(fun, start, **kw):
            for _ in range(801):
                fun(start)

        with patch(
            "physics_guided_optimization_selection.core.least_squares",
            side_effect=exceed,
        ):
            with self.assertRaises(RuntimeError) as caught:
                fit_stage(Reference(), d, y, 432, np.zeros(54), "A", 1)
        self.assertEqual(caught.exception.diagnostic_stage["nfev"], 800)
        self.assertEqual(
            caught.exception.diagnostic_stage["optimization_forward_calls"], 800
        )
        for task, n, stage in [("B", 432, 1), ("A", 252, 1), ("B", 252, 5)]:
            d, y = fixture(n)
            with self.assertRaises(ValueError):
                fit_stage(Reference(), d, y, n, np.zeros(54), task, stage)

    def test_internal_selection_ties_failure_and_window_isolation(self):
        zero = np.zeros((180, 4))
        windows = {
            n: {
                r: inner_scores(zero + error, zero, n, 612)
                for r, error in [("A", 2.0), ("B", 1.0)]
            }
            for n in (252, 342, 432)
        }
        self.assertEqual(select_recipe(612, windows)["selected_recipe"], "B")
        windows[342]["B"] = dict(valid=False, error="fixture failure")
        self.assertEqual(select_recipe(612, windows)["selected_recipe"], "A")
        windows[252]["A"] = dict(valid=False, error="fixture failure")
        self.assertIsNone(select_recipe(612, windows)["selected_recipe"])
        one = {252: {r: inner_scores(zero + 1, zero, 252, 432) for r in ("A", "B")}}
        self.assertEqual(select_recipe(432, one)["selected_recipe"], "A")
        one[252]["B"]["mae_mm"] = [0.8] * 4
        self.assertEqual(select_recipe(432, one)["selected_recipe"], "B")
        one[252]["B"]["forecast_rmse"] = 0.0
        with self.assertRaises(ValueError):
            select_recipe(432, one)
        with self.assertRaises(ValueError):
            select_recipe(432, windows)
        with self.assertRaises(ValueError):
            inner_scores(zero, zero, 432, 432)

    def test_future_label_perturbation_cannot_change_training_or_selection_inputs(self):
        path = (
            ROOT / "results/ootang_bplus_v1_2/20260910_diagnostics/input_prefix_792.csv"
        )
        frame = pd.read_csv(path)
        for outer in (432, 612):
            changed = frame.copy()
            changed.loc[
                outer:,
                [p for p in frame.columns if p.endswith("/mm") and p != "Rainfall/mm"],
            ] += 100000
            with tempfile.TemporaryDirectory() as tmp:
                paths = [Path(tmp) / "old.csv", Path(tmp) / "changed.csv"]
                frame.to_csv(paths[0], index=False)
                changed.to_csv(paths[1], index=False)
                outputs = []
                for p in paths:
                    d, y = read_input(p, outer)
                    internal = (252,) if outer == 432 else (252, 342, 432)
                    windows = {
                        n: {
                            r: inner_scores(
                                np.zeros((180, 4)) + j, y[n : n + 180], n, outer
                            )
                            for j, r in enumerate(("A", "B"))
                        }
                        for n in internal
                    }
                    outputs.append(
                        (
                            array_sha(y),
                            array_sha(d.forcing),
                            select_recipe(outer, windows),
                        )
                    )
                self.assertEqual(outputs[0], outputs[1])

    def test_score_denominators_station_means_and_date_endpoints(self):
        y = np.zeros((612, 4))
        mu = y + np.array([1.0, 2.0, 3.0, 4.0])
        rows = metric_rows(mu, y, 432, "A")
        means = [r for r in rows if r["station"] == "four_point_mean"]
        self.assertEqual([r["days"] for r in means], [402, 180])
        self.assertEqual([r["rmse_mm"] for r in means], [2.5, 2.5])
        score = inner_scores(mu[252:432], y[252:432], 252, 432)
        self.assertEqual(
            (score["first_date"], score["last_date"]), ("2017-03-10", "2017-09-05")
        )

    def test_locks_reject_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock.json"
            seal(path, dict(selected="A"))
            with self.assertRaises(FileExistsError):
                seal(path, dict(selected="B"))
            self.assertEqual(json.loads(path.read_text()), dict(selected="A"))

    def test_comparison_requires_both_errors_in_both_phases(self):
        metrics = []
        choices = {}
        for n in (432, 612):
            choices[str(n)] = dict(T="A", V=dict(selected_recipe="B"))
            for recipe in ("A", "B"):
                for phase in ("train", "prediction"):
                    for point in ("ATU1", "ATU5", "MJ3", "MJ1"):
                        value = 10.0 if recipe == "A" else 9.0
                        mae = value
                        if recipe == "B" and point == "MJ3" and phase == "prediction":
                            mae = 10.1
                        if recipe == "B" and point == "MJ1":
                            value = 10.0 - 5e-7
                        metrics.append(
                            dict(
                                fit_days=n,
                                candidate=recipe,
                                phase=phase,
                                station=point,
                                rmse_mm=value,
                                mae_mm=mae,
                                bias_mm=0.0,
                            )
                        )
        _, accepted = compare(pd.DataFrame(metrics), choices)
        self.assertEqual(int(accepted.simultaneously_improved.sum()), 4)
        self.assertEqual(
            set(accepted[accepted.simultaneously_improved].station), {"ATU1", "ATU5"}
        )
        choices["432"]["V"]["selected_recipe"] = None
        _, accepted = compare(pd.DataFrame(metrics), choices)
        self.assertFalse(accepted[accepted.fit_days == 432].evaluable.any())


if __name__ == "__main__":
    unittest.main()
