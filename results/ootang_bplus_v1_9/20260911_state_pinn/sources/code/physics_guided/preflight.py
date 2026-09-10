"""Frozen-source numerical reproduction, independent of neural model effectiveness."""

import numpy as np
import pandas as pd
from .reference import frozen_theta, sha, save_json
from .data import read_data, POINTS


def reproduce(ref, drivers, observed, output):
    archived, labels = read_data(ref.PARENT / "work/monitoring.csv")
    delta_y = float(np.max(abs(observed - labels)))
    delta_f = float(np.max(abs(drivers.forcing - archived.forcing)))
    if delta_y > 1e-8 or delta_f > 1e-10 or not drivers.dates.equals(archived.dates):
        raise ValueError("Repository data do not match the frozen source columns")
    th, y0 = frozen_theta(ref)
    if not np.allclose(y0, drivers.y0, atol=1e-9, rtol=0):
        raise ValueError("Frozen y0 differs")
    ctx = ref.Context(drivers.forcing)
    with np.load(ref.ROOT / "results/geometry_matrices.npz") as g:
        for name, now in [
            ("T", ctx.T),
            ("observation", ctx.obs),
            ("kt", ctx.kt),
            ("bulk", ctx.bulk),
            ("length", ctx.length),
        ]:
            np.testing.assert_allclose(now, g[name], rtol=1e-12, atol=1e-12)
    u, states = ref.forward(th, ctx, states=True)
    mu = drivers.y0 + u
    with np.load(ref.ROOT / "results/curves.npz") as curves:
        errors = {k: float(np.max(abs(v - curves[k]))) for k, v in states.items()}
        displacement_error = float(np.max(abs(mu - curves["predicted"])))
    csv = pd.read_csv(ref.ROOT / "output/future_prediction.csv")
    csv_error = float(
        np.max(abs(mu[1168:] - csv[[p + "_predicted_mm" for p in POINTS]].to_numpy()))
    )
    prefix = ref.forward(th, ref.Context(drivers.forcing[:1168]))
    prefix_error = float(np.max(abs(prefix - u[:1168])))
    if max(displacement_error, csv_error) > 0.001 or prefix_error > 1e-8:
        raise ArithmeticError("Frozen reproduction failed; stop dependent training")
    if errors["rain_head"] > 1e-10 or errors["moisture"] > 1e-12:
        raise ArithmeticError("Frozen hydrology differs")
    if any(errors[k] > 1e-5 for k in ("coordinates", "plastic", "background")):
        raise ArithmeticError("Frozen internal mechanics differ")
    metrics = {}
    for phase, ix in [
        ("fit_original_1168", slice(0, 1168)),
        ("historical_prediction_293", slice(1168, None)),
    ]:
        residual = mu[ix] - observed[ix]
        metrics[phase] = dict(
            rmse=np.sqrt(np.mean(residual**2, axis=0)).tolist(),
            mae=np.mean(abs(residual), axis=0).tolist(),
            pooled_rmse=float(np.sqrt(np.mean(residual**2))),
        )
    result = dict(
        status="passed",
        data_hashes={
            "repository": sha("data/monitoring_data.csv"),
            "archive_csv": sha(ref.PARENT / "work/monitoring.csv"),
        },
        max_label_difference_mm=delta_y,
        max_driver_difference=delta_f,
        displacement_max_abs_mm=displacement_error,
        csv_max_abs_mm=csv_error,
        state_max_abs_errors=errors,
        prefix_max_abs_mm=prefix_error,
        observation_matrix=ctx.obs.tolist(),
        original_metrics=metrics,
    )
    save_json(output / "reproduction.json", result)
    np.savez_compressed(output / "frozen_reference.npz", mu=mu, **states)
    return result
