"""Independent audit of the v1.1 completed experiment, skipped before artifact creation."""

from pathlib import Path
import json
import sys
import unittest
import numpy as np
import pandas as pd
from scipy.special import ndtr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
from physics_guided.data import read_data, POINTS

RUN = ROOT / "results/ootang_bplus_v1_1/20260910_implementation"


@unittest.skipUnless(
    (RUN / "predictions_final.csv").exists(),
    "Completed final artifacts not yet available",
)
class PhysicsGuidedArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.predictions = pd.read_csv(RUN / "predictions.csv", parse_dates=["date"])
        cls.metrics = pd.read_csv(RUN / "metrics.csv")
        cls.drivers, cls.y = read_data(ROOT / "data/monitoring_data.csv")

    def test_complete_rows_axes_and_warmup(self):
        p = self.predictions
        self.assertEqual(len(p), (1168 + 1461) * 4 * 9)
        self.assertFalse(
            p.duplicated(["stage", "model", "component", "date", "station"]).any()
        )
        for (stage, model, component, station), frame in p.groupby(
            ["stage", "model", "component", "station"]
        ):
            n = 1168 if stage == "development" else 1461
            frame = frame.sort_values("date")
            self.assertEqual(len(frame), n)
            self.assertEqual(set(frame.status), {"warmup", "valid"})
            self.assertEqual((frame.status == "warmup").sum(), 30)
            self.assertTrue(np.isfinite(frame.mean_mm.iloc[30:]).all())
            self.assertEqual(
                int(frame.mean_mm.isna().sum()), 29 if model == "M1" else 0
            )
            if model == "M0":
                self.assertTrue(frame.std_mm.isna().all())
                self.assertTrue(frame.filter(regex="lower_|upper_").isna().all().all())
            else:
                self.assertTrue((frame.std_mm.iloc[30:] > 0).all())
                self.assertEqual(int(frame.std_mm.isna().sum()), 30)

    def test_mixture_moments_and_cdf_quantiles(self):
        p = self.predictions
        for (stage, model, point), frame in p[
            (p.model != "M0") & (p.status == "valid")
        ].groupby(["stage", "model", "station"]):
            seeds = [
                frame[frame.component == f"seed_{i}"].sort_values("date")
                for i in range(3)
            ]
            mixed = frame[frame.component == "mixture"].sort_values("date")
            mu = np.stack([s.mean_mm.to_numpy() for s in seeds])
            sigma = np.stack([s.std_mm.to_numpy() for s in seeds])
            expected_mu = mu.mean(axis=0)
            expected_std = np.sqrt(np.mean(sigma**2 + (mu - expected_mu) ** 2, axis=0))
            np.testing.assert_allclose(mixed.mean_mm, expected_mu, atol=1e-10, rtol=0)
            np.testing.assert_allclose(mixed.std_mm, expected_std, atol=1e-10, rtol=0)
            for level in [80, 90, 95]:
                for side, target in [
                    ("lower", (1 - level / 100) / 2),
                    ("upper", 1 - (1 - level / 100) / 2),
                ]:
                    q = mixed[f"{side}_{level}_mm"].to_numpy()
                    probability = ndtr((q - mu) / sigma).mean(axis=0)
                    tolerance = 1e-6 / (sigma.min(axis=0) * np.sqrt(2 * np.pi)) + 1e-12
                    self.assertTrue(np.all(abs(probability - target) <= tolerance))
            bounds = mixed[
                [
                    "lower_95_mm",
                    "lower_90_mm",
                    "lower_80_mm",
                    "upper_80_mm",
                    "upper_90_mm",
                    "upper_95_mm",
                ]
            ].to_numpy()
            self.assertTrue(np.all(np.diff(bounds, axis=1) > 0))

    def test_every_station_mean_metric_recomputed_from_observations(self):
        table = self.metrics
        for (stage, model, component, station, phase), frame in self.predictions[
            self.predictions.status == "valid"
        ].groupby(["stage", "model", "component", "station", "phase"]):
            index = self.drivers.dates.get_indexer(frame.date)
            y = self.y[index, POINTS.index(station)]
            residual = frame.mean_mm.to_numpy() - y
            values = {
                "rmse": np.sqrt(np.mean(residual**2)),
                "mae": np.mean(abs(residual)),
            }
            rows = table[
                (table.stage == stage)
                & (table.model == model)
                & (table.component == component)
                & (table.station == station)
                & (table.phase == phase)
            ]
            for metric, expected in values.items():
                row = rows[rows.metric == metric].iloc[0]
                self.assertAlmostEqual(row.value, expected, places=9)
                self.assertEqual(row.valid_days, len(frame))

    def test_final_epochs_were_locked_and_all_seeds_completed(self):
        selection = json.loads((RUN / "selection.json").read_text())
        self.assertTrue(selection["frozen_before_final"])
        for model in ["M1", "M2"]:
            dev = json.loads(
                (RUN / "development" / model / "selection.json").read_text()
            )
            final = json.loads((RUN / "final" / model / "selection.json").read_text())
            self.assertEqual(final["seeds"], [0, 1, 2])
            self.assertEqual(
                (final["e_mu"], final["e_sigma"]), (dev["e_mu"], dev["e_sigma"])
            )
            for seed in range(3):
                mean_log = json.loads(
                    (
                        RUN / "final" / model / f"mean_training_seed{seed}.json"
                    ).read_text()
                )
                scale_log = json.loads(
                    (
                        RUN / "final" / model / f"scale_training_seed{seed}.json"
                    ).read_text()
                )
                self.assertEqual(len(mean_log), dev["e_mu"])
                self.assertEqual(len(scale_log), dev["e_sigma"])

    def test_m2_full_trajectory_constraint_records(self):
        for stage, n in [("development", 1168), ("final", 1461)]:
            for seed in range(3):
                with np.load(RUN / stage / "M2" / f"states_seed{seed}.npz") as arrays:
                    states, audit, multiplier = (
                        arrays["states"],
                        arrays["substep_audit"],
                        arrays["multipliers"],
                    )
                    self.assertEqual(states.shape, (n, 24))
                    self.assertEqual(audit.shape, (n - 1, 5))
                    self.assertTrue(np.isfinite(states).all())
                    self.assertGreaterEqual(float(audit[:, 0].min()), -1e-8)
                    self.assertGreaterEqual(float(audit[:, 1].min()), -1e-7)
                    self.assertLessEqual(float(audit[:, 2].max()), 1e-7)
                    self.assertGreaterEqual(float(audit[:, 3].min()), 0)
                    self.assertTrue(np.all((multiplier >= 0.5) & (multiplier <= 2)))


if __name__ == "__main__":
    unittest.main()
