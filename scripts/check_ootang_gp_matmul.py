"""Bounded post-run warning check against explicit sums; no fitting or changed predictions."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

import joblib
import numpy as np
from threadpoolctl import threadpool_limits

from physics_guided_gp import POINTS, ROOT, read_npz, sha, specification, write_json


def main():
    spec = specification()
    out = ROOT / spec["output_dir"]
    saved = read_npz(ROOT / spec["reference"])
    prediction = read_npz(out / "predictions.npz")
    inputs = read_npz(out / "inputs.npz")
    normalizers = json.loads((out / "normalizers.json").read_text())
    lock = json.loads((out / "prediction_lock.json").read_text())
    for path, digest in lock["files"].items():
        if sha(out / path) != digest:
            raise AssertionError("Locked artifact changed")
    rows = []
    with threadpool_limits(limits=1):
        for j, point in enumerate(POINTS):
            gp = joblib.load(out / f"{point}.joblib")
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                mean = gp.predict(inputs["x"])
            cross = gp.kernel_.k1(inputs["x"], gp.X_train_)
            # optimize=False uses explicit products/sums, not BLAS matrix multiplication.
            explicit = np.einsum("ij,j->i", cross, gp.alpha_, optimize=False)
            scale = normalizers["residual_denominator_mm"][j]
            full = saved["mean"][:, j] + explicit * scale
            if not all(
                np.isfinite(x).all() for x in (cross, gp.alpha_, mean, explicit, full)
            ):
                raise AssertionError("Nonfinite operand or result")
            np.testing.assert_allclose(
                full,
                prediction["mean"][:, j],
                atol=spec["matrix_atol"],
                rtol=spec["matrix_rtol"],
            )
            rows.append(
                dict(
                    station=point,
                    warning_messages=[str(w.message) for w in caught],
                    warning_categories=[w.category.__name__ for w in caught],
                    all_operands_and_results_finite=True,
                    explicit_sum_to_saved_mean_max_difference_mm=float(
                        abs(full - prediction["mean"][:, j]).max()
                    ),
                    library_to_explicit_sum_max_difference_mm=float(
                        abs(mean - explicit).max() * scale
                    ),
                )
            )
    report = dict(
        checked_utc=datetime.now(timezone.utc).isoformat(),
        script_sha256=sha(__file__),
        prediction_lock_sha256=sha(out / "prediction_lock.json"),
        new_fit_calls=0,
        physical_solver_calls=0,
        labels_read=0,
        predictions_changed=False,
        point_checks=rows,
        finite_and_explicit_sum_checks_passed=True,
        scope="Observed matmul warnings retained; warning origin not established. This check only verifies saved mean values.",
    )
    write_json(ROOT / "results/ootang_bplus_gp_v1/matmul_warning_check.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
