"""Independent saved-curve, lineage, objective and selector replay; zero fitting."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from physics_guided.data import POINTS
from physics_guided.calibration import initial
from physics_guided.reference import ROOT, sha, load
from physics_guided_diagnostics.run import REFERENCE
from physics_guided_diagnostics.core import array_sha
from .core import read_input, INNER
from .run import read_json, seal, verify_index


def objective(mu, y, weight, alpha):
    e = (mu - y) / 100
    return float(
        np.sum(e**2)
        + weight * np.sum(e[-1] ** 2)
        + alpha * len(e) / (len(e) - 30) * np.sum((e[30:] - e[:-30]) ** 2)
    )


def verify(out):
    manifest = read_json(out / "manifest.json")
    task = manifest["task"]
    completion = read_json(out / "completion.json")
    for path, digest in read_json(out / "protected_before.json").items():
        assert sha(ROOT / path) == digest, path
    for path, digest in manifest["sources"].items():
        assert sha(ROOT / path) == digest, path
        assert sha(out / "source_snapshot" / path) == digest, path
    if (out / "artifact_manifest.json").exists():
        verify_index(out)
    ref = load(REFERENCE)
    total_nfev = total_calls = 0
    stage_count = 0
    for scope in sorted(out.glob("[AB]_*")):
        if not scope.is_dir():
            continue
        parts = scope.name.split("_")
        n = int(parts[1])
        d, y = read_input(out / "input_prefix_792.csv", n)
        if task == "A":
            anchor = read_json(scope / "anchor_locked.json")
            valid = {k: v for k, v in anchor["candidates"].items() if v["valid"]}
            best = None
            for k, v in valid.items():
                mu = np.load(scope / f"{k}.npz")["mean"]
                np.testing.assert_allclose(
                    objective(mu, y, 100 * n, 1), v["objective"], atol=1e-7, rtol=0
                )
                sr = v["source_record"]
                assert sha(ROOT / sr["source"]) == sr["source_sha256"]
                assert sr["fit_days"] == n and sr["training_label_sha256"] == array_sha(
                    y
                )
                assert sr["training_forcing_sha256"] == array_sha(d.forcing)
                if best is None or v["objective"] < valid[best]["objective"] - 1e-12:
                    best = k
            assert best == anchor["selected"]
            preceding = np.array(valid[best]["theta"])
        else:
            preceding = initial(ref, parts[2])
        for path in sorted(scope.glob("stage*.json")):
            r = read_json(path)
            stage = r["stage"]
            expected_weight = (
                100 * n if task == "A" else n * [0, 1, 100, 100][stage - 1]
            )
            budget = 800 if task == "A" else [900, 800, 800, 800][stage - 1]
            assert (r["lambda_T"], r["max_nfev"], r["fit_days"]) == (
                expected_weight,
                budget,
                n,
            )
            assert r["fit_last_date"] == str(d.dates[-1].date())
            assert r["training_label_sha256"] == array_sha(y)
            assert r["training_forcing_sha256"] == array_sha(d.forcing)
            np.testing.assert_array_equal(r["input_theta"], preceding)
            clipped = np.clip(preceding, ref.LO + 1e-9, ref.HI - 1e-9)
            np.testing.assert_array_equal(r["optimizer_initial_theta"], clipped)
            x = np.array(r["theta"])
            assert np.isfinite(x).all() and (x >= ref.LO).all() and (x <= ref.HI).all()
            for vector, key in [
                (preceding, "input_objective"),
                (clipped, "optimizer_initial_objective"),
                (x, "objective"),
            ]:
                mu = ref.forward(vector, ref.Context(d.forcing)) + d.y0
                np.testing.assert_allclose(
                    objective(mu, y, expected_weight, task == "A"),
                    r[key],
                    atol=1e-7,
                    rtol=0,
                )
            assert 0 < r["nfev"] <= budget
            assert r["optimization_forward_calls"] == r["nfev"] + 55 * r["njev"]
            assert r["gtol_met"] == (r["optimality"] <= 1e-7)
            total_nfev += r["nfev"]
            total_calls += r["optimization_forward_calls"]
            stage_count += 1
            preceding = x
        if task == "A":
            retained = read_json(scope / "retained_locked.json")
            path = scope / "attempt.npz"
            choose_attempt = False
            if path.exists() and retained["attempt_valid"]:
                j = objective(np.load(path)["mean"], y, 100 * n, 1)
                choose_attempt = valid[best]["objective"] - j > 1e-7
            assert retained["retained"] == ("attempt" if choose_attempt else "anchor")
            expected = (
                np.load(path)["theta"]
                if choose_attempt
                else np.array(valid[best]["theta"])
            )
            np.testing.assert_array_equal(retained["theta"], expected)
    if not completion["incomplete_stages"]:
        assert total_nfev == completion["new_optimizer_nfev"]
        assert total_calls == completion["optimization_forward_calls"]
    assert total_nfev <= completion["cap_nfev"]
    trajectories = 0
    substeps = 0
    max_error = 0.0
    for path in sorted(out.rglob("*.npz")):
        a = np.load(path)
        n = len(a["mean"])
        d, _ = read_input(out / "input_prefix_792.csv", n)
        np.testing.assert_array_equal(a["dates"], d.dates.strftime("%Y-%m-%d"))
        mu = ref.forward(a["theta"], ref.Context(d.forcing)) + d.y0
        error = float(abs(mu - a["mean"]).max())
        max_error = max(max_error, error)
        assert error <= 1e-8
        checks = a["substep_audit"]
        assert checks.shape == (n - 1, 5) and np.isfinite(checks).all()
        assert checks[:, 0].min() >= -1e-8 and checks[:, 1].min() >= -1e-7
        assert checks[:, 2].max() <= 1e-7 and checks[:, 3].min() >= 0
        audit = read_json(path.with_suffix(".audit.json"))
        assert audit["substeps_checked"] == 64 * (n - 1)
        assert audit["zero_trajectory_max_error_mm"] <= 1e-5
        substeps += 64 * (n - 1)
        trajectories += 1
    accepted = 0
    if task == "B":
        lock = read_json(out / "selection_locked.json")
        assert lock["parameter_lock_sha256"] == sha(
            out / "fitted_parameters_locked.json"
        )
        assert lock["inner_scores_sha256"] == sha(out / "inner_scores_locked.json")
        done = read_json(out / "outer_scoring_completed.json")
        assert done["selection_lock_sha256"] == sha(out / "selection_locked.json")
        assert pd.Timestamp(done["completed_utc"]) >= pd.Timestamp(lock["locked_utc"])
        windows = read_json(out / "inner_scores_locked.json")
        metrics = pd.read_csv(out / "metrics.csv")
        predictions = pd.read_csv(out / "predictions.csv")
        assert not predictions.duplicated(
            ["fit_days", "recipe", "date", "station"]
        ).any()
        acceptance = pd.read_csv(out / "acceptance.csv")
        expected_metrics = {}
        for outer in INNER:
            _, y = read_input(out / "input_prefix_792.csv", outer)
            means = {}
            for recipe in ("A", "B"):
                rmse = []
                mae = []
                for n in INNER[outer]:
                    score = windows[str(outer)][str(n)][recipe]
                    if not score["valid"]:
                        break
                    mu = np.load(out / f"inner_{outer}_{n}_{recipe}.npz")["mean"]
                    e = mu[n:] - y[n : n + 180]
                    r = np.sqrt(np.mean(e**2, axis=0))
                    m = np.mean(abs(e), axis=0)
                    np.testing.assert_allclose(r, score["rmse_mm"], atol=1e-9, rtol=0)
                    np.testing.assert_allclose(m, score["mae_mm"], atol=1e-9, rtol=0)
                    rmse.extend(r)
                    mae.extend(m)
                else:
                    means[recipe] = (float(np.mean(rmse)), float(np.mean(mae)))
            chosen = None
            for recipe, (r, m) in means.items():
                if (
                    chosen is None
                    or r < means[chosen][0] - 1e-6
                    or (
                        abs(r - means[chosen][0]) <= 1e-6
                        and m < means[chosen][1] - 1e-6
                    )
                ):
                    chosen = recipe
            assert chosen == lock["selections"][str(outer)]["V"]["selected_recipe"]
            d, y = read_input(out / "input_prefix_792.csv", outer + 180)
            j0s = {}
            for recipe in ("A", "B"):
                mu = np.load(out / f"outer_{outer}_{recipe}.npz")["mean"]
                j0s[recipe] = objective(mu[:outer], y[:outer], 100 * outer, 0)
                p = predictions[
                    (predictions.fit_days == outer) & (predictions.recipe == recipe)
                ]
                matrix = p.pivot(index="date", columns="station", values="mean_mm")[
                    list(POINTS)
                ].to_numpy()
                observed = p.pivot(
                    index="date", columns="station", values="observed_mm"
                )[list(POINTS)].to_numpy()
                np.testing.assert_allclose(matrix, mu, atol=1e-9, rtol=0)
                np.testing.assert_allclose(observed, y, atol=1e-9, rtol=0)
                for phase, sl in [
                    ("train", slice(30, outer)),
                    ("prediction", slice(outer, outer + 180)),
                ]:
                    e = mu[sl] - y[sl]
                    fields = dict(
                        rmse_mm=np.sqrt(np.mean(e**2, axis=0)),
                        mae_mm=np.mean(abs(e), axis=0),
                        bias_mm=np.mean(e, axis=0),
                    )
                    for j, point in enumerate([*POINTS, "four_point_mean"]):
                        row = metrics[
                            (metrics.fit_days == outer)
                            & (metrics.candidate == recipe)
                            & (metrics.phase == phase)
                            & (metrics.station == point)
                        ]
                        assert len(row) == 1 and row.iloc[0].days == len(e)
                        values = {
                            key: float(v[j] if j < 4 else v.mean())
                            for key, v in fields.items()
                        }
                        expected_metrics[(outer, recipe, phase, point)] = values
                        for key, value in values.items():
                            np.testing.assert_allclose(
                                row.iloc[0][key], value, atol=1e-9, rtol=0
                            )
            t = "B" if j0s["B"] < j0s["A"] - 1e-12 else "A"
            assert t == lock["selections"][str(outer)]["T"]
            for point in POINTS:
                differences = []
                if chosen is not None:
                    for phase in ("train", "prediction"):
                        aa = expected_metrics[(outer, t, phase, point)]
                        bb = expected_metrics[(outer, chosen, phase, point)]
                        differences.extend(aa[k] - bb[k] for k in ("rmse_mm", "mae_mm"))
                good = len(differences) == 4 and all(v > 1e-6 for v in differences)
                row = acceptance[
                    (acceptance.fit_days == outer) & (acceptance.station == point)
                ]
                assert (
                    len(row) == 1 and bool(row.iloc[0].simultaneously_improved) == good
                )
                accepted += int(good)
        assert accepted == read_json(out / "summary.json")["selected_improved"]
    return dict(
        status="passed",
        task=task,
        optimizer_rerun=False,
        stages_checked=stage_count,
        trajectories_checked=trajectories,
        substeps_checked=substeps,
        saved_curve_max_error_mm=max_error,
        nfev=total_nfev,
        optimization_forward_calls=total_calls,
        selected_improved=accepted if task == "B" else None,
        protected_files_checked=len(read_json(out / "protected_before.json")),
        scientific_sources_checked=len(manifest["sources"]),
        user_acceptance="pending",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = verify(args.output)
    if args.write:
        seal(args.output / "verification.json", result)
    print(result)
