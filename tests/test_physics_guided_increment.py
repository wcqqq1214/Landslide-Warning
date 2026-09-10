"""Analytic loss tests and independent verification of the bounded v1.3 artifacts."""

import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from physics_guided.data import POINTS
from physics_guided.reference import ROOT, load, sha
from physics_guided.calibration import initial
from physics_guided_diagnostics.core import (
    read_prefix,
    residual_vector,
    bounded_jacobian,
    array_sha,
)
from physics_guided_diagnostics.run import REFERENCE
from physics_guided_increment.objective import (
    residual,
    components,
    fit_stage,
    increment_rows,
    specification,
)
from physics_guided_increment.report import comparisons


class IncrementUnitTests(unittest.TestCase):
    def test_fixed_spec_matches_versioned_config(self):
        self.assertEqual(
            specification(),
            json.loads((ROOT / "config/ootang_bplus_increment.v1_3.json").read_text()),
        )

    def test_alpha_zero_exactly_reproduces_original_residual_and_jacobian(self):
        t = np.arange(432, dtype=float)[:, None]
        y = np.broadcast_to(np.sin(t / 30), (432, 4))
        x = np.array([0.5, 1.0 - 1e-8])

        def prediction(z):
            return t * z[0] + np.arange(1.0, 5.0) * z[1]

        def old(z):
            return residual_vector(prediction(z), y, 43200)

        def new(z):
            return residual(prediction(z), y, 43200, alpha=0)

        np.testing.assert_array_equal(new(x), old(x))
        np.testing.assert_array_equal(
            bounded_jacobian(new, x, np.ones(2)), bounded_jacobian(old, x, np.ones(2))
        )

    def test_constant_offset_cancels_and_ramp_has_known_30_day_error(self):
        n = 432
        t = np.arange(n, dtype=float)[:, None]
        target = np.broadcast_to(t * 2, (n, 4)).copy()
        offset = target + np.array([1.0, 2.0, 4.0, 8.0])
        self.assertEqual(components(offset, target, n)["increment_objective"], 0.0)
        np.testing.assert_array_equal(
            residual(offset, target, n)[4 * n + 4 :], np.zeros(4 * (n - 30))
        )
        slope = np.array([0.1, 0.2, -0.3, 0.4])
        prediction = target + t * slope
        expected_increment = n * np.sum((30 * slope) ** 2) / 10000
        terms = components(prediction, target, 100 * n)
        self.assertAlmostEqual(
            terms["increment_objective"], expected_increment, places=10
        )
        self.assertAlmostEqual(
            float(np.sum(residual(prediction, target, 100 * n) ** 2)),
            terms["joint_objective"],
            places=8,
        )

    def test_increment_derivative_and_boundary_target_dates(self):
        n = 432
        t = np.arange(n, dtype=float)[:, None]
        slopes = np.array([1.0, 2.0, -1.0, 3.0])

        def fun(x):
            return residual(t * slopes * x[0], np.zeros((n, 4)), 0)

        jac = bounded_jacobian(fun, np.array([1.0 - 1e-8]), np.ones(1))
        expected = np.tile(np.sqrt(n / (n - 30)) * 30 * slopes / 100, n - 30)
        np.testing.assert_allclose(jac[4 * n + 4 :, 0], expected, atol=1e-8)
        total = n + 180
        truth = np.zeros((total, 4))
        predicted = truth.copy()
        predicted[n:] = 100
        rows = increment_rows(predicted, truth, n, "step")
        training = [r for r in rows if r["phase"] == "train"]
        future = [r for r in rows if r["phase"] == "prediction"]
        self.assertTrue(
            all(r["days"] == n - 30 and r["delta30_rmse_mm"] == 0 for r in training)
        )
        self.assertTrue(all(r["days"] == 180 for r in future))
        for row in future:
            self.assertAlmostEqual(row["delta30_rmse_mm"], np.sqrt(30 * 100**2 / 180))
            self.assertAlmostEqual(row["delta30_mae_mm"], 30 * 100 / 180)

    def test_fitter_accepts_only_the_training_prefix_and_fixed_budget(self):
        drivers, labels, _ = read_prefix(ROOT / "data/monitoring_data.csv")
        lengths = []

        class Reference:
            LO = np.full(54, -1.0)
            HI = np.ones(54)

            def Context(self, forcing):
                lengths.append(len(forcing))
                return forcing

            def forward(self, theta, context):
                return np.zeros((len(context), 4))

        ref = Reference()

        def optimize(fun, x, **kwargs):
            self.assertEqual(kwargs["max_nfev"], 800)
            self.assertEqual(kwargs["gtol"], 1e-7)
            vector = fun(x)
            self.assertEqual(len(vector), 4 * 432 + 4 + 4 * (432 - 30))
            return SimpleNamespace(
                x=x,
                cost=np.sum(vector**2) / 2,
                nfev=1,
                njev=0,
                success=False,
                status=0,
                message="unit fixture",
                optimality=1.0,
            )

        with patch(
            "physics_guided_increment.objective.least_squares", side_effect=optimize
        ):
            record = fit_stage(
                ref,
                drivers.prefix(432),
                labels[:432],
                432,
                np.zeros(54),
                43200,
                800,
                "fixture",
            )
        self.assertEqual(lengths, [432])
        self.assertEqual(record["training_label_sha256"], array_sha(labels[:432]))
        with self.assertRaises(ValueError):
            fit_stage(
                ref,
                drivers.prefix(432),
                labels,
                432,
                np.zeros(54),
                43200,
                800,
                "future labels",
            )
        with self.assertRaises(ValueError):
            fit_stage(ref, drivers, labels, 792, np.zeros(54), 79200, 800, "extra fold")
        with self.assertRaises(ValueError):
            fit_stage(
                ref,
                drivers.prefix(432),
                labels[:432],
                432,
                np.zeros(54),
                43200,
                801,
                "extra budget",
            )

    def test_acceptance_requires_all_four_displacement_metrics(self):
        metrics, increments, selections = [], [], {}
        for n in (432, 612):
            selections[str(n)] = {
                f"{arm}_final": {"selected_start": "A"} for arm in ("C0", "C1")
            }
            for arm in ("C0", "C1"):
                for start in ("A", "B"):
                    for phase in ("train", "prediction"):
                        for point in [*POINTS, "four_point_mean"]:
                            key = dict(
                                fit_days=n,
                                candidate=f"{arm}_{start}_final",
                                phase=phase,
                                station=point,
                                days=n - 30 if phase == "train" else 180,
                            )
                            value = 9.0 if arm == "C1" else 10.0
                            # One worse metric disqualifies MJ3; the 1e-6 tolerance disqualifies MJ1.
                            mae = (
                                11.0
                                if arm == "C1"
                                and point == "MJ3"
                                and phase == "prediction"
                                else value
                            )
                            if arm == "C1" and point == "MJ1":
                                value = 10.0 - 5e-7
                            metrics.append(
                                dict(**key, rmse_mm=value, mae_mm=mae, bias_mm=0.0)
                            )
                            increments.append(
                                dict(**key, delta30_rmse_mm=0.0, delta30_mae_mm=0.0)
                            )
        _, accepted = comparisons(
            pd.DataFrame(metrics), pd.DataFrame(increments), selections
        )
        selected = accepted[accepted.comparison == "selected"]
        self.assertEqual(int(selected.simultaneously_improved.sum()), 4)
        self.assertEqual(
            set(selected[selected.simultaneously_improved].station), {"ATU1", "ATU5"}
        )


class IncrementArtifactTests(unittest.TestCase):
    def test_completed_artifacts_independently(self):
        out = ROOT / "results/ootang_bplus_v1_3/20260910_increment"
        if not (out / "summary.json").exists():
            self.skipTest("Increment run and export have not completed")
        control = ROOT / specification()["control_run"]
        completion = json.loads((out / "completion.json").read_text())
        self.assertEqual(completion["status"], "complete")
        manifest = json.loads((out / "manifest.json").read_text())
        self.assertEqual(
            manifest["input_csv_sha256"], sha(control / "input_prefix_792.csv")
        )
        self.assertEqual(
            manifest["control_manifest_sha256"], sha(control / "artifact_manifest.json")
        )
        for path, digest in json.loads(
            (out / "protected_before.json").read_text()
        ).items():
            self.assertEqual(sha(ROOT / path), digest, path)
        for path, digest in manifest["sources"].items():
            self.assertEqual(sha(out / "source_snapshot" / path), digest, path)
            self.assertEqual(sha(ROOT / path), digest, path)
        drivers, labels, _ = read_prefix(out / "input_prefix_792.csv")
        metrics = pd.read_csv(out / "metrics.csv")
        increments = pd.read_csv(out / "increment_metrics.csv")
        predictions = pd.read_csv(out / "predictions.csv")
        accepted = pd.read_csv(out / "acceptance.csv")
        selection = json.loads((out / "selection.json").read_text())
        summary = json.loads((out / "summary.json").read_text())
        ref = load(REFERENCE)
        total_nfev = total_forward = total_accepted = 0
        expected_scores = {}
        self.assertEqual(len(predictions), 6 * 4 * (612 + 792))
        self.assertFalse(
            predictions.duplicated(["fit_days", "candidate", "date", "station"]).any()
        )
        for n in (432, 612):
            scope = out / f"fit_{n}"
            end = n + 180
            for start in ("A", "B"):
                preceding = initial(ref, start)
                for number, (weight, budget) in enumerate(
                    zip([0, n, 100 * n, 100 * n], [900, 800, 800, 800]), 1
                ):
                    record = json.loads(
                        (scope / start / f"stage{number}.json").read_text()
                    )
                    theta = np.array(record["theta"])
                    np.testing.assert_array_equal(record["input_theta"], preceding)
                    np.testing.assert_array_equal(
                        record["optimizer_initial_theta"],
                        np.clip(preceding, ref.LO + 1e-9, ref.HI - 1e-9),
                    )
                    self.assertTrue(
                        np.isfinite(theta).all()
                        and (theta >= ref.LO).all()
                        and (theta <= ref.HI).all()
                    )
                    self.assertEqual(
                        (record["lambda_T"], record["max_nfev"]), (weight, budget)
                    )
                    self.assertEqual(
                        record["training_label_sha256"], array_sha(labels[:n])
                    )
                    self.assertEqual(
                        record["training_forcing_sha256"],
                        array_sha(drivers.forcing[:n]),
                    )
                    self.assertLessEqual(record["nfev"], budget)
                    self.assertEqual(
                        record["optimization_forward_calls"],
                        record["nfev"] + 55 * record["njev"],
                    )
                    error = (
                        ref.forward(theta, ref.Context(drivers.forcing[:n]))
                        + drivers.y0
                        - labels[:n]
                    )
                    expected = float(
                        (
                            np.sum(error**2)
                            + weight * np.sum(error[-1] ** 2)
                            + n / (n - 30) * np.sum((error[30:] - error[:-30]) ** 2)
                        )
                        / 10000
                    )
                    self.assertAlmostEqual(record["objective"], expected, places=7)
                    total_nfev += record["nfev"]
                    total_forward += record["optimization_forward_calls"]
                    preceding = theta
            for group in ("C0_final", "C1_stage3", "C1_final"):
                arm, stage = group.split("_")
                calculated_objectives = {}
                for start in ("A", "B"):
                    name = f"{arm}_{start}_{stage}"
                    arrays = np.load(scope / f"{name}.npz")
                    mu = arrays["mean"]
                    self.assertEqual(mu.shape, (end, 4))
                    self.assertTrue(np.isfinite(mu).all())
                    np.testing.assert_array_equal(
                        arrays["dates"], drivers.dates[:end].strftime("%Y-%m-%d")
                    )
                    frame = predictions[
                        (predictions.fit_days == n) & (predictions.candidate == name)
                    ].pivot(
                        index="date",
                        columns="station",
                        values=["mean_mm", "observed_mm"],
                    )
                    np.testing.assert_allclose(
                        frame["mean_mm"][list(POINTS)].to_numpy(), mu, atol=1e-9, rtol=0
                    )
                    np.testing.assert_allclose(
                        frame["observed_mm"][list(POINTS)].to_numpy(),
                        labels[:end],
                        atol=1e-9,
                        rtol=0,
                    )
                    e = mu - labels[:end]
                    j0 = float(
                        (np.sum(e[:n] ** 2) + 100 * n * np.sum(e[n - 1] ** 2)) / 10000
                    )
                    jdelta = float(
                        n / (n - 30) * np.sum((e[30:n] - e[: n - 30]) ** 2) / 10000
                    )
                    audit = json.loads((scope / f"{name}_audit.json").read_text())
                    self.assertTrue(audit["valid"])
                    self.assertAlmostEqual(audit["original_objective"], j0, places=7)
                    self.assertAlmostEqual(
                        audit["increment_objective"], jdelta, places=8
                    )
                    calculated_objectives[start] = j0 if arm == "C0" else j0 + jdelta
                    self.assertAlmostEqual(
                        audit["objective"], calculated_objectives[start], places=7
                    )
                    self.assertEqual(audit["substeps_checked"], 64 * (end - 1))
                    self.assertLessEqual(audit["zero_trajectory_max_error_mm"], 1e-5)
                    self.assertLessEqual(audit["prefix_error_mm"], 1e-8)
                    checks = arrays["substep_audit"]
                    self.assertEqual(checks.shape, (end - 1, 5))
                    self.assertTrue(np.isfinite(checks).all())
                    self.assertGreaterEqual(checks[:, 0].min(), -1e-8)
                    self.assertGreaterEqual(checks[:, 1].min(), -1e-7)
                    self.assertLessEqual(checks[:, 2].max(), 1e-7)
                    self.assertGreaterEqual(checks[:, 3].min(), 0.0)
                    if arm == "C0":
                        old = np.load(control / f"fit_{n}/{start}_continued.npz")
                        np.testing.assert_array_equal(arrays["theta"], old["theta"])
                        np.testing.assert_allclose(mu, old["mean"], atol=1e-8, rtol=0)
                    for phase, sl in [
                        ("train", slice(30, n)),
                        ("prediction", slice(n, end)),
                    ]:
                        error = e[sl]
                        times = np.arange(end)[sl]
                        delta_error = (mu[times] - mu[times - 30]) - (
                            labels[times] - labels[times - 30]
                        )
                        expected = dict(
                            rmse_mm=np.sqrt(np.mean(error**2, axis=0)),
                            mae_mm=np.mean(abs(error), axis=0),
                            bias_mm=np.mean(error, axis=0),
                            delta30_rmse_mm=np.sqrt(np.mean(delta_error**2, axis=0)),
                            delta30_mae_mm=np.mean(abs(delta_error), axis=0),
                        )
                        for j, point in enumerate([*POINTS, "four_point_mean"]):
                            key = (n, name, phase, point)
                            expected_scores[key] = {
                                k: float(v[j] if j < 4 else v.mean())
                                for k, v in expected.items()
                            }
                            for table, fields in [
                                (metrics, ["rmse_mm", "mae_mm", "bias_mm"]),
                                (increments, ["delta30_rmse_mm", "delta30_mae_mm"]),
                            ]:
                                row = table[
                                    (table.fit_days == n)
                                    & (table.candidate == name)
                                    & (table.phase == phase)
                                    & (table.station == point)
                                ]
                                self.assertEqual(len(row), 1)
                                self.assertEqual(row.iloc[0].days, len(error))
                                for field in fields:
                                    self.assertAlmostEqual(
                                        float(row.iloc[0][field]),
                                        expected_scores[key][field],
                                        places=8,
                                    )
                expected_start = (
                    "B"
                    if calculated_objectives["B"] < calculated_objectives["A"] - 1e-12
                    else "A"
                )
                self.assertEqual(
                    selection[str(n)][group]["selected_start"], expected_start
                )
            old_choice = json.loads((control / f"fit_{n}/selection.json").read_text())[
                "continued"
            ]["selected_start"]
            self.assertEqual(
                selection[str(n)]["C0_final"]["selected_start"], old_choice
            )
        self.assertEqual(total_nfev, completion["new_optimizer_nfev"])
        self.assertEqual(total_nfev, summary["new_optimizer_nfev"])
        self.assertLessEqual(total_nfev, 13200)
        self.assertEqual(total_forward, completion["optimization_forward_calls"])
        self.assertEqual(total_forward, summary["optimization_forward_calls"])
        for _, row in accepted.iterrows():
            differences = []
            for phase in ("train", "prediction"):
                c0 = expected_scores[
                    (row.fit_days, f"C0_{row.C0_start}_final", phase, row.station)
                ]
                c1 = expected_scores[
                    (row.fit_days, f"C1_{row.C1_start}_final", phase, row.station)
                ]
                differences.extend([c0[k] - c1[k] for k in ("rmse_mm", "mae_mm")])
            expected = all(d > 1e-6 for d in differences)
            self.assertEqual(row.simultaneously_improved, expected)
            total_accepted += int(expected and row.comparison == "selected")
        self.assertEqual(summary["selected_improved"], total_accepted)
        self.assertEqual(summary["selected_total"], 8)
        for _, row in pd.read_csv(out / "comparison.csv").iterrows():
            for arm, start in [("C0", row.C0_start), ("C1", row.C1_start)]:
                scores = expected_scores[
                    (row.fit_days, f"{arm}_{start}_final", row.phase, row.station)
                ]
                for field, value in scores.items():
                    self.assertAlmostEqual(
                        float(row[f"{arm}_{field}"]), value, places=8
                    )


if __name__ == "__main__":
    unittest.main()
