"""Input isolation, numerical accounting and independent v1.2 artifact checks."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from physics_guided.reference import ROOT
from physics_guided.data import POINTS
from physics_guided_diagnostics.core import (
    read_prefix,
    validate_training,
    residual_vector,
    bounded_jacobian,
    choose_start,
    metric_rows,
    growth_rows,
)


class DiagnosticsUnitTests(unittest.TestCase):
    def test_reader_does_not_parse_future_rows(self):
        drivers, labels, frame = read_prefix(ROOT / "data/monitoring_data.csv")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "prefix.csv"
            frame.to_csv(path, index=False)
            with path.open("a") as handle:
                handle.write("invalid,future,rows,must,not,be,parsed,extra,column\n")
            changed, changed_labels, _ = read_prefix(path)
        self.assertEqual(len(changed.dates), 792)
        np.testing.assert_allclose(changed_labels, labels, atol=1e-10, rtol=0)
        np.testing.assert_allclose(changed.forcing, drivers.forcing, atol=1e-10, rtol=0)

    def test_training_api_rejects_longer_labels_and_origin_changes(self):
        d, y, _ = read_prefix(ROOT / "data/monitoring_data.csv")
        validate_training(d.prefix(432), y[:432], 432)
        with self.assertRaises(ValueError):
            validate_training(d.prefix(432), y, 432)
        with self.assertRaises(ValueError):
            validate_training(d, y, 700)
        changed = y[:432].copy()
        changed[0] += 1
        with self.assertRaises(ValueError):
            validate_training(d.prefix(432), changed, 432)

    def test_terminal_weight_and_bound_aware_derivative(self):
        p = np.arange(12, dtype=float).reshape(3, 4)
        r = residual_vector(p, np.zeros_like(p), 300)
        self.assertAlmostEqual(
            float(r @ r),
            float((p * p).sum() / 10000 + 300 * (p[-1] ** 2).sum() / 10000),
        )
        x = np.array([0.3, 1.0 - 1e-8])
        j = bounded_jacobian(
            lambda z: np.array([z[0] ** 2 + 3 * z[1], z[0] - z[1] ** 2]), x, np.ones(2)
        )
        np.testing.assert_allclose(j, [[0.6, 3], [1, -2 * x[1]]], atol=3e-6)

    def test_training_selection_excludes_failure_and_does_not_see_forecast(self):
        records = {
            "A": dict(valid=True, objective=4.0, forecast_rmse=1.0),
            "B": dict(valid=True, objective=3.0, forecast_rmse=100.0),
        }
        self.assertEqual(choose_start(records), "B")
        records["B"]["valid"] = False
        self.assertEqual(choose_start(records), "A")
        records["B"] = dict(valid=True, objective=4.0 - 5e-13)
        self.assertEqual(choose_start(records), "A")
        self.assertIsNone(choose_start({"A": dict(valid=False)}))

    def test_metrics_and_growth_use_correct_days_and_axes(self):
        d, y, _ = read_prefix(ROOT / "data/monitoring_data.csv")
        n = 432
        days = n + 180
        t = np.arange(days, dtype=float)[:, None]
        mu = np.broadcast_to(t * 2, (days, 4)).copy()
        target = mu - np.array([1.0, 2.0, 3.0, 4.0])
        records = metric_rows(mu, target, n, "synthetic")
        aggregate = [r for r in records if r["station"] == "four_point_mean"]
        self.assertEqual([r["days"] for r in aggregate], [402, 180])
        self.assertEqual([r["rmse_mm"] for r in aggregate], [2.5, 2.5])
        state = dict(background=mu * 0.25, coordinates=mu)
        rates = growth_rows(
            d.prefix(days), target, mu, state, np.eye(4), n, "synthetic"
        )
        self.assertEqual(len(rates), 20)
        for r in rates:
            self.assertEqual(r["predicted_mm_day"], 2.0)
            self.assertEqual(r["background_mm_day"] + r["other_state_mm_day"], 2.0)
        self.assertEqual(
            [r["days"] for r in rates if r["window"] == "prediction_all"], [180] * 4
        )


class DiagnosticsArtifactTests(unittest.TestCase):
    def test_completed_artifacts_independently(self):
        out = ROOT / "results/ootang_bplus_v1_2/20260910_diagnostics"
        if not (out / "completion.json").exists():
            self.skipTest("Diagnostic run has not completed")
        preservation = json.loads((out / "preservation.json").read_text())
        self.assertTrue(
            all(
                preservation[k]
                for k in ["protected_unchanged", "source_unchanged", "plan_unchanged"]
            )
        )
        total_nfev = 0
        drivers, labels, _ = read_prefix(out / "input_prefix_792.csv")
        for n in (432, 612, 792):
            scope = out / f"fit_{n}"
            selection = json.loads((scope / "selection.json").read_text())
            score = pd.read_csv(scope / "metrics.csv")
            pred = pd.read_csv(scope / "predictions.csv")
            length = n + 180 if n < 792 else n
            self.assertEqual(len(pred), length * 4 * 4)
            self.assertFalse(pred.duplicated(["candidate", "date", "station"]).any())
            self.assertEqual(
                pd.to_datetime(pred.date).max(),
                pd.Timestamp("2016-07-01") + pd.Timedelta(days=length - 1),
            )
            for (name, phase, station), frame in pred[pred.phase != "warmup"].groupby(
                ["candidate", "phase", "station"]
            ):
                e = frame.mean_mm.to_numpy() - frame.observed_mm.to_numpy()
                row = score[
                    (score.candidate == name)
                    & (score.phase == phase)
                    & (score.station == station)
                ].iloc[0]
                self.assertAlmostEqual(
                    row.rmse_mm, float(np.sqrt(np.mean(e**2))), places=8
                )
                self.assertAlmostEqual(row.mae_mm, float(np.mean(abs(e))), places=8)
            for variant in ("baseline", "continued"):
                self.assertEqual(
                    selection[variant]["selected_start"],
                    choose_start(selection[variant]["candidates"]),
                )
                for start in ("A", "B"):
                    name = f"{start}_{variant}"
                    record = json.loads((scope / f"{name}.json").read_text())
                    arrays = np.load(scope / f"{name}.npz")
                    self.assertEqual(arrays["mean"].shape, (length, 4))
                    np.testing.assert_allclose(
                        arrays["coordinates"] @ arrays["observation_matrix"].T
                        + drivers.y0,
                        arrays["mean"],
                        atol=1e-9,
                        rtol=0,
                    )
                    frame = pred[pred.candidate == name].pivot(
                        index="date",
                        columns="station",
                        values=["mean_mm", "observed_mm"],
                    )
                    np.testing.assert_allclose(
                        frame["observed_mm"][list(POINTS)].to_numpy(),
                        labels[:length],
                        atol=1e-9,
                        rtol=0,
                    )
                    np.testing.assert_allclose(
                        frame["mean_mm"][list(POINTS)].to_numpy(),
                        arrays["mean"],
                        atol=1e-9,
                        rtol=0,
                    )
                    r = (
                        frame["mean_mm"][list(POINTS)].to_numpy()[:n]
                        - frame["observed_mm"][list(POINTS)].to_numpy()[:n]
                    ) / 100
                    expected = float(
                        (r * r).sum() + record["lambda_T"] * (r[-1] * r[-1]).sum()
                    )
                    self.assertAlmostEqual(expected, record["objective"], places=7)
                    self.assertEqual(record["fit_days"], n)
                    audit = json.loads((scope / f"{name}_audit.json").read_text())
                    self.assertTrue(audit["valid"])
                    self.assertEqual(audit["substeps_checked"], 64 * (length - 1))
                    if variant == "continued":
                        baseline = json.loads(
                            (scope / f"{start}_baseline.json").read_text()
                        )
                        np.testing.assert_array_equal(
                            record["input_theta"], baseline["theta"]
                        )
            growth = pd.read_csv(scope / "growth.csv")
            for _, row in growth.iterrows():
                observations = pred[
                    (pred.candidate == row.candidate) & (pred.station == row.station)
                ].set_index("date")
                self.assertEqual(
                    (pd.Timestamp(row.end) - pd.Timestamp(row.start)).days, row.days
                )
                expected = (
                    observations.loc[row.end, "mean_mm"]
                    - observations.loc[row.start, "mean_mm"]
                ) / row.days
                self.assertAlmostEqual(row.predicted_mm_day, expected, places=9)
                self.assertAlmostEqual(
                    row.predicted_mm_day,
                    row.background_mm_day + row.other_state_mm_day,
                    places=9,
                )
            paths = list(scope.glob("*_stage[123].json")) + list(
                scope.glob("*_continued.json")
            )
            total_nfev += sum(json.loads(p.read_text())["nfev"] for p in paths)
        self.assertLessEqual(total_nfev, 14800)


if __name__ == "__main__":
    unittest.main()
