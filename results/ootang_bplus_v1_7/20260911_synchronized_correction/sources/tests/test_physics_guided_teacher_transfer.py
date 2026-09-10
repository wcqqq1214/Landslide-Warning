from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from physics_guided_teacher_transfer.core import (
    CUTOFFS,
    POINTS,
    decompose,
    matched_comparisons,
    periods,
    read_drivers,
    score,
    teacher_features,
)


class TeacherTransferTests(unittest.TestCase):
    def test_zero_and_fixed_correction_decomposition(self):
        old = np.zeros((180, 4))
        new = old + 5
        physical, correction, total = decompose(old, new, old, new)
        np.testing.assert_array_equal(physical, total)
        np.testing.assert_array_equal(correction, old)
        physical, correction, total = decompose(old, new, old + 3, new + 1)
        np.testing.assert_array_equal(physical, old + 5)
        np.testing.assert_array_equal(correction, old - 2)
        np.testing.assert_array_equal(total, old + 3)
        with self.assertRaises(ValueError):
            decompose(old, new[:-1], old, new)
        with self.assertRaises(ArithmeticError):
            decompose(old, new, old, new * np.nan)

    def test_same_branch_gain_does_not_imply_current_baseline_gain(self):
        rows = []
        for n in CUTOFFS:
            for branch in ("OLD", "NEW"):
                for strategy in ("P0", "IN", "OOF"):
                    value = {
                        ("OLD", "P0"): 10,
                        ("OLD", "IN"): 5,
                        ("OLD", "OOF"): 0.5,
                        ("NEW", "P0"): 1,
                        ("NEW", "IN"): 2,
                        ("NEW", "OOF"): 0.5,
                    }[branch, strategy]
                    for station in POINTS:
                        for part in ("outer_train", "prediction"):
                            rows.append(
                                dict(
                                    outer_days=n,
                                    branch=branch,
                                    strategy=strategy,
                                    station=station,
                                    part=part,
                                    rmse_mm=value,
                                    mae_mm=value,
                                    crps_mm=value,
                                )
                            )
        result = matched_comparisons(pd.DataFrame(rows))
        old_inside = result[(result.branch == "OLD") & (result.strategy == "IN")]
        self.assertTrue(old_inside.strict_improvement_vs_own.all())
        self.assertFalse(old_inside.strict_improvement_vs_current.any())
        self.assertTrue(
            result[result.strategy == "OOF"].strict_improvement_vs_current.all()
        )

    def test_periods_preserve_dates_and_scoring_denominators(self):
        for n, c in CUTOFFS.items():
            self.assertEqual(
                periods(n),
                {
                    "old_fit_context": (30, c),
                    "scale_context": (c, n),
                    "outer_train": (30, n),
                    "prediction": (n, n + 180),
                },
            )
            base = np.zeros((1, n + 180, 4))
            rows, _ = score(
                base, np.ones((1, 4)), np.full((n + 180, 4), 3), n, "OLD", "P0"
            )
            self.assertEqual(len(rows), 16)
            for row in rows:
                a, b = periods(n)[row["part"]]
                self.assertEqual(row["days"], b - a)
                self.assertEqual(row["rmse_mm"], 3)
                self.assertEqual(row["bias_mm"], -3)

    def test_prediction_interface_never_parses_later_displacement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            data = pd.DataFrame(
                {
                    "Date": pd.date_range("2016-07-01", periods=792).strftime(
                        "%Y-%m-%d"
                    ),
                    "Rainfall/mm": np.ones(792),
                    "RWL/m": np.full(792, 150),
                    **{p + "/mm": np.arange(792, dtype=float) for p in POINTS},
                }
            )
            data.to_csv(path, index=False)
            original = read_drivers(path, 432)
            for p in POINTS:
                data[p + "/mm"] = data[p + "/mm"].astype(object)
                data.loc[1:, p + "/mm"] = "must not be parsed"
            data.loc[612:, "Date"] = "invalid future date"
            data.to_csv(path, index=False)
            changed = read_drivers(path, 432)
            np.testing.assert_array_equal(original.forcing, changed.forcing)
            np.testing.assert_array_equal(original.y0, changed.y0)
            self.assertTrue(original.dates.equals(changed.dates))
            with self.assertRaises(ValueError):
                read_drivers(path, 612)

    def test_registered_extension_can_pass_old_180_day_horizon(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            pd.DataFrame(
                {
                    "Date": pd.date_range("2016-07-01", periods=612).strftime(
                        "%Y-%m-%d"
                    ),
                    "Rainfall/mm": np.ones(612),
                    "RWL/m": np.full(612, 150),
                    **{p + "/mm": np.zeros(612) for p in POINTS},
                }
            ).to_csv(path, index=False)
            drivers = read_drivers(path, 432)
            trajectory = Path(directory) / "old.npz"
            np.savez_compressed(
                trajectory,
                dates=drivers.dates.strftime("%Y-%m-%d").to_numpy(dtype="U10"),
                mean=np.zeros((612, 4)),
                rain_head=np.zeros((612, 4)),
                moisture=np.zeros((612, 4)),
                reservoir_head=np.zeros(612),
            )
            mean, features = teacher_features(trajectory, drivers, 432)
            self.assertEqual(mean.shape, (612, 4))
            self.assertEqual(features.shape, (612, 20, 4))
            with self.assertRaises(ValueError):
                teacher_features(trajectory, drivers, 612)


if __name__ == "__main__":
    unittest.main()
