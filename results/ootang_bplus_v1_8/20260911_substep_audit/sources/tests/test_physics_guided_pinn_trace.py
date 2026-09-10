from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from physics_guided_pinn.run_substep_audit import read_drivers, specification
from physics_guided_pinn.substep_audit import audit_trace
from physics_guided_pinn.trace import (
    compile_trace,
    inputs_from_saved,
    instrument,
    integrate,
    original_function,
    restore_function,
    source_bytes,
)


class PinnTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.library = compile_trace(Path(cls.temporary.name) / "native")
        cls.raw = source_bytes()
        cls.length = np.array([1.0, 2.0, 3.0, 4.0])
        cls.observation = np.array(
            [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 0.8, 0.2], [0, 0, 0.1, 0.9]]
        )
        cls.y0 = np.array([10.0, 20.0, 30.0, 40.0])
        theta = np.zeros(54)
        theta[20:24] = np.log([1.0, 2.0, 3.0, 4.0])
        theta[44:47] = 1
        cls.saved = dict(
            theta=theta,
            force=np.array(
                [[0.0, 0, 0, 0], [2.0, -1.0, 0.8, -0.5], [-0.5, 2.0, -0.5, 1.0]]
            ),
            rain_head=np.array(
                [[0.0, 0, 0, 0], [0.02, 0.01, 0.03, 0.04], [0.01, 0.03, 0.02, 0.05]]
            ),
            background=np.array(
                [[0.0, 0, 0, 0], [0.01, 0.02, 0.03, 0.04], [0.02, 0.01, 0.04, 0.08]]
            ),
            kc=np.array(
                [
                    [0.2, -0.03, 0, 0],
                    [-0.03, 0.2, -0.02, 0],
                    [0, -0.02, 0.1, -0.01],
                    [0, 0, -0.01, 0.1],
                ]
            ),
            ke=np.eye(4) * 0.15,
        )
        inputs = inputs_from_saved(cls.saved, cls.length)
        # Two short synthetic calls, zero Ootang trajectories or parameter fitting.
        cls.original = integrate(cls.library, inputs, traced=False)
        cls.recorded = integrate(cls.library, inputs)
        cls.saved.update(cls.original)
        cls.saved["mean"] = cls.original["coordinates"] @ cls.observation.T + cls.y0
        cls.tolerance = specification()["tolerances"]

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_instrumentation_restores_original_function_and_pins_source(self):
        derived = instrument(self.raw)
        canonical = self.raw.decode().replace("\r\n", "\n")
        self.assertTrue(derived.startswith(canonical + "\n"))
        traced = derived[len(canonical) + 1 :]
        self.assertEqual(restore_function(traced), original_function(canonical))
        with self.assertRaises(ValueError):
            instrument(self.raw + b"\n")

    def test_instrumentation_preserves_native_outputs_and_switching(self):
        for name in (
            "coordinates",
            "plastic",
            "contact",
            "bulk_reaction",
            "basal_reaction",
            "bad",
        ):
            np.testing.assert_array_equal(self.recorded[name], self.original[name])
        self.assertGreater(len(np.unique(self.recorded["masks"][:, 0])), 1)
        self.assertEqual(self.recorded["previous"].shape, (128, 24))
        np.testing.assert_array_equal(
            self.recorded["previous"][1:], self.recorded["current"][:-1]
        )

    def test_numpy_update_and_pinn_residual_audits_pass_synthetic_trace(self):
        result = audit_trace(
            self.recorded,
            self.saved,
            self.length,
            self.observation,
            self.y0,
            self.tolerance,
        )
        self.assertTrue(result["passed"], result["checks"])
        self.assertEqual(result["substeps"], 128)
        self.assertLess(
            result["checks"]["daily_observation"]["max_absolute_error"], 1e-12
        )

    def test_state_load_and_activity_corruption_are_detected(self):
        for field, index, amount in (
            ("current", (10, 0), 1.0),
            ("loads", (10, 0), 100.0),
            ("masks", (10, 1), -1),
        ):
            with self.subTest(field=field):
                changed = {k: v.copy() for k, v in self.recorded.items()}
                changed[field][index] += amount
                result = audit_trace(
                    changed,
                    self.saved,
                    self.length,
                    self.observation,
                    self.y0,
                    self.tolerance,
                )
                self.assertFalse(result["passed"])

    def test_driver_reader_never_parses_later_displacement_labels(self):
        path = Path(self.temporary.name) / "drivers.csv"
        frame = pd.DataFrame(
            {
                "Date": pd.date_range("2016-07-01", periods=792),
                "Rainfall/mm": 1.0,
                "RWL/m": 150.0,
            }
        )
        for name, first in zip(("ATU1", "ATU5", "MJ3", "MJ1"), self.y0):
            frame[name + "/mm"] = [str(first)] + ["invalid future label"] * 791
        frame.to_csv(path, index=False)
        dates, forcing, y0 = read_drivers(path)
        self.assertEqual(len(dates), 792)
        self.assertEqual(forcing.shape, (792, 2))
        np.testing.assert_array_equal(y0, self.y0)


if __name__ == "__main__":
    unittest.main()
