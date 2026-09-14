"""Preserve NumPy matrix warnings and check finite saved-ridge replay by fsum."""

import math
import warnings

import numpy as np

from .core import ROOT, load_npz, spec, utc, write_json


def main():
    cfg = spec()
    records = []
    for phase, (n, end) in cfg["stages"].items():
        data = load_npz(
            ROOT / cfg["reuse_tcn"] / f"implementation_verification/teacher_{n}.npz"
        )
        ridge = load_npz(ROOT / cfg["reuse_tcn"] / phase / "ridge.npz")
        z = (data["x"] - ridge["x_mean"]) / ridge["x_std"]
        assert np.isfinite(z).all() and np.isfinite(ridge["coef"]).all()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            matrix = z @ ridge["coef"]
        explicit = np.array(
            [
                [
                    math.fsum(
                        float(z[t, j]) * float(ridge["coef"][j, p])
                        for j in range(z.shape[1])
                    )
                    for p in range(4)
                ]
                for t in range(end)
            ]
        )
        assert np.isfinite(matrix).all() and np.isfinite(explicit).all()
        np.testing.assert_allclose(matrix, explicit, atol=1e-8, rtol=0)
        raw = ridge["y0"] + (explicit + ridge["intercept"]) * ridge["unit"]
        saved = load_npz(
            ROOT / cfg["reuse_regularization"] / phase / "reused_predictions.npz"
        )["RR_COND"]
        np.testing.assert_allclose(raw[n:], saved, atol=1e-8, rtol=0)
        records.append(
            dict(
                phase=phase,
                rows=end,
                input_finite=True,
                both_replays_finite=True,
                warnings=[
                    dict(category=w.category.__name__, message=str(w.message))
                    for w in caught
                ],
                matrix_fsum_max_abs_difference=float(abs(matrix - explicit).max()),
                saved_prediction_max_abs_difference_mm=float(
                    abs(raw[n:] - saved).max()
                ),
            )
        )
    result = dict(
        status="passed_with_recorded_warnings",
        time_utc=utc(),
        numpy_version=np.__version__,
        records=records,
        new_fits=0,
        optimizer_updates=0,
        core_or_saved_results_changed=False,
        interpretation="Finite outputs match explicit scalar summation; underlying NumPy/BLAS warning cause is not established or claimed fixed.",
    )
    write_json(ROOT / cfg["out"] / "verification_v1/linear_warning_check.json", result)
    print(result)


if __name__ == "__main__":
    main()
