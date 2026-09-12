"""Re-score the saved v2.3 run and audit raw neural residuals without training/C calls."""

import argparse
import hashlib
import json
from pathlib import Path
import types

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_npz(path):
    with np.load(path, allow_pickle=False) as data:
        return {key: data[key] for key in data.files}


def verify(out):
    manifest = json.loads((out / "artifact_manifest.json").read_text())["files"]
    actual = {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()}
    assert actual == set(manifest) | {"artifact_manifest.json"}, (
        "Artifact inventory changed"
    )
    for name, record in manifest.items():
        assert (
            sha(out / name) == record["sha256"]
            and (out / name).stat().st_size == record["bytes"]
        ), name
    source = json.loads((out / "source_snapshot.json").read_text())["files"]
    for name, digest in source.items():
        assert sha(out / "sources" / name) == digest, name
    spec = json.loads(
        (out / "sources/config/ootang_probability_pinn.v2_3.json").read_text()
    )
    for name, digest in spec["source_sha256"].items():
        # The core evolved only before this run; all inputs remain pinned.
        path = (
            out / "sources" / name if (out / "sources" / name).exists() else ROOT / name
        )
        assert sha(path) == digest, name
    execution = json.loads((out / "execution.json").read_text())
    assert execution["status"] == "completed"
    assert execution["optimizer_updates"] == 900 and execution["updates_by_seed"] == {
        "0": 300,
        "1": 300,
        "2": 300,
    }
    assert execution["final_stage_started"] is False
    lock = json.loads((out / "prediction_lock.json").read_text())
    assert lock["epochs"] == [300, 300, 300] and lock["seeds"] == [0, 1, 2]
    assert lock["predictions_sha256"] == sha(out / "selected_predictions.npz")
    assert lock["normalizers_sha256"] == sha(out / "normalizers.json")
    assert lock["labels_used_for_selection"] is False
    probability = types.ModuleType("frozen_probability")
    source_path = out / "sources/code/physics_guided/probability.py"
    # Execute the already hash-checked pure scorer without creating bytecode
    # cache files inside the immutable experiment directory.
    exec(
        compile(source_path.read_text(), str(source_path), "exec"), probability.__dict__
    )
    n, h = spec["end_days"], spec["fit_days"]
    observed_frame = pd.read_csv(ROOT / spec["input"], nrows=n)
    dates = pd.to_datetime(observed_frame.Date)
    assert list(dates) == list(pd.date_range("2016-07-01", periods=n))
    y = observed_frame[[p + "/mm" for p in POINTS]].to_numpy(float)
    fit = read_npz(out / "fit_labels.npz")
    assert np.array_equal(fit["observed"], y[:h])
    reference = read_npz(out / "reference.npz")
    selected = read_npz(out / "selected_predictions.npz")
    assert np.array_equal(reference["dates"], selected["dates"])
    assert np.array_equal(selected["dates"], dates.dt.strftime("%Y-%m-%d").to_numpy())
    old = read_npz(
        ROOT / spec["source_run"] / "development/M1/selected_predictions.npz"
    )
    distributions = {
        "M0": (reference["mean"][None], None),
        "v1.1-e0": (old["means"], old["sigmas"]),
        "PINN": (selected["means"], selected["sigmas"]),
        "replay_diagnostic": (selected["reference_means"], selected["sigmas"]),
    }
    expected = {}
    for name, (mu, sigma) in distributions.items():
        for phase, mask in (("train", slice(30, h)), ("prediction", slice(h, n))):
            error = mu[:, mask].mean(axis=0) - y[mask]
            vals = {
                "mae": abs(error).mean(axis=0),
                "rmse": np.sqrt((error**2).mean(axis=0)),
            }
            if sigma is not None:
                m, s = mu[:, mask], sigma[:, mask]
                q = probability.summarize(m, s)
                vals["crps"] = probability.crps(m, s, y[mask]).mean(axis=0)
                for level in (80, 90, 95):
                    low, high = q[f"lower_{level}"], q[f"upper_{level}"]
                    vals[f"coverage_{level}"] = (
                        (low <= y[mask]) & (y[mask] <= high)
                    ).mean(axis=0)
                    vals[f"width_{level}"] = (high - low).mean(axis=0)
                    vals[f"interval_score_{level}"] = probability.interval_score(
                        y[mask], low, high, level
                    ).mean(axis=0)
            for metric, vector in vals.items():
                for station, value in zip(
                    (*POINTS, "point_mean"), (*vector, np.mean(vector))
                ):
                    expected[(name, phase, station, metric)] = value
            expected[(name, phase, "pooled", "rmse")] = np.sqrt(np.mean(error**2))
    table = pd.read_csv(out / "metrics.csv")
    assert not table.duplicated(["model", "phase", "station", "metric"]).any()
    actual = {
        (r.model, r.phase, r.station, r.metric): r.value
        for r in table.itertuples(index=False)
    }
    assert set(actual) == set(expected)
    metric_difference = max(abs(actual[k] - v) for k, v in expected.items())
    assert metric_difference < 1e-8
    daily = pd.read_csv(out / "daily_predictions.csv")
    assert (
        len(daily) == n * 4 * 4
        and not daily.duplicated(["model", "date", "station"]).any()
    )
    csv_difference = 0.0
    for name, (mu, _) in distributions.items():
        d = daily[daily.model == name]
        assert np.array_equal(d.date.to_numpy(), np.repeat(reference["dates"], 4))
        assert np.array_equal(d.station.to_numpy(), np.tile(POINTS, n))
        np.testing.assert_allclose(
            d.observed_mm.to_numpy(), y.ravel(), atol=1e-9, rtol=0
        )
        error = abs(d.mean_mm.to_numpy() - mu.mean(axis=0).ravel())
        csv_difference = max(csv_difference, float(np.nanmax(error)))
    assert csv_difference < 1e-8
    assert daily.loc[daily.model == "M0", "crps_mm"].isna().all()
    raw_difference = 0.0
    for seed in range(3):
        path = out / f"seed_{seed}"
        assert sha(path / "e300.pt") == lock["checkpoint_sha256"][str(seed)]
        values = read_npz(path / "prediction.npz")
        assert np.array_equal(values["mean"], selected["means"][seed])
        assert np.array_equal(values["sigma"], selected["sigmas"][seed])
        native = read_npz(path / "reference_trace.npz")
        native_mean = (
            np.einsum(
                "ni,ji->nj",
                native["coordinates"],
                reference["observation_matrix"],
                optimize=False,
            )
            + reference["y0"]
        )
        np.testing.assert_allclose(
            native_mean, selected["reference_means"][seed], rtol=0, atol=1e-8
        )
        assert json.loads((path / "reference_audit.json").read_text())["passed"]
        st = values["states"]
        assert st.shape == ((n - 1) * 64 + 1, 24) and np.isfinite(st).all()
        assert np.array_equal(st[0], np.zeros(24))
        s, p, rb, rc, re, b = np.split(st, 6, axis=1)
        th, length = reference["theta"], reference["length"]
        state_mean = (
            np.einsum(
                "ni,ji->nj",
                (s + b)[::64],
                reference["observation_matrix"],
                optimize=False,
            )
            + reference["y0"]
        )
        np.testing.assert_allclose(state_mean, values["mean"], atol=1e-8, rtol=0)
        multiplier = np.exp(np.log(2) * values["a"])
        assert ((multiplier >= 0.5) & (multiplier <= 2)).all()
        np.testing.assert_allclose(
            values["a"][:h].mean(axis=0), np.zeros(4), rtol=0, atol=1e-14
        )
        rate = reference["background_rate"] * multiplier
        transient = th[28:32] * (1 - np.exp(-np.arange(n)[:, None] / np.exp(th[32:36])))
        background = (
            np.vstack((np.zeros((1, 4)), np.cumsum(rate[1:], axis=0))) + transient
        )
        np.testing.assert_allclose(background, values["background"], atol=1e-8, rtol=0)
        fraction = np.tile(np.arange(1, 65) / 64, n - 1)[:, None]
        day = np.repeat(np.arange(1, n), 64)
        expected_b = background[day - 1] + fraction * (
            background[day] - background[day - 1]
        )
        np.testing.assert_allclose(b[1:], expected_b, atol=1e-8, rtol=0)
        expected_db = np.diff(np.vstack((np.zeros((1, 4)), expected_b)), axis=0)
        dp, du = np.diff(p, axis=0), np.diff(s + b, axis=0)
        dt = 1 / 64
        beta, ar = dt / (np.exp(th[20:24]) + dt), 1 / (1 + dt / np.exp(th[16:20]))
        ac, ae = 1 / (1 + dt / np.exp(th[42])), 1 / (1 + dt / np.exp(th[43]))
        force = np.repeat(reference["force"][1:], 64, axis=0)
        elastic = np.repeat(
            (reference["rain_head"] * th[[44, 45, 46, 46]])[1:], 64, axis=0
        )
        gap = np.exp(th[8:12]) * length * dp / dt + rb[1:] + rc[1:] + re[1:] - force

        def mm(a, m):
            return np.einsum("ni,ji->nj", a, m, optimize=False)

        raw = dict(
            motion=np.diff(s, axis=0) - beta * (p[1:] + elastic - s[:-1]),
            basal_memory=rb[1:] - ar * (rb[:-1] + np.exp(th[12:16]) * length * dp),
            contact_memory=rc[1:] - ac * (rc[:-1] + mm(du, reference["kc"])),
            bulk_memory=re[1:] - ae * (re[:-1] + mm(du, reference["ke"])),
            background=np.diff(b, axis=0) - expected_db,
            slip_step=beta * dp,
            yield_gap=gap,
            negative_slip=np.maximum(-beta * dp, 0),
            negative_gap=np.maximum(-gap, 0),
            complementarity=beta * dp * gap,
        )
        summary = json.loads((path / "neural_residuals.json").read_text())
        for key, value in raw.items():
            stats = dict(
                max_absolute=float(abs(value).max()),
                minimum=float(value.min()),
                rms=float(np.sqrt(np.mean(value**2))),
            )
            for stat, scalar in stats.items():
                delta = abs(summary[key][stat] - scalar)
                assert delta <= 1e-8 + 1e-10 * abs(scalar), (seed, key, stat, delta)
                raw_difference = max(raw_difference, delta)

    def vector(model, metric, phase="prediction"):
        return np.array([expected[(model, phase, p, metric)] for p in POINTS])

    tol, fraction = spec["score_tolerance_mm"], spec["approximation_gain_fraction"]
    gain = {
        key: vector("M0" if key == "mae" else "v1.1-e0", key) - vector("PINN", key)
        for key in ("mae", "crps", "interval_score_90")
    }
    error = np.abs(selected["means"][:, h:] - selected["reference_means"][:, h:]).mean(
        axis=(0, 1)
    )
    checks = dict(
        prediction_mae_each_lower=bool((gain["mae"] > tol).all()),
        fit_mae_each_not_worse=bool(
            (vector("PINN", "mae", "train") <= vector("M0", "mae", "train") + tol).all()
        ),
        mean_approximation_small_relative_to_gain=bool(
            ((gain["mae"] > tol) & (error <= fraction * gain["mae"])).all()
        ),
    )
    for key in ("crps", "interval_score_90"):
        primary, replay = vector("PINN", key), vector("replay_diagnostic", key)
        positive = gain[key] > tol
        checks[key + "_average_lower"] = bool(gain[key].mean() > tol)
        checks[key + "_each_not_worse"] = bool((gain[key] >= -tol).all())
        checks[key + "_approximation_small_relative_to_gain"] = bool(
            gain[key].mean() > tol
            and abs(primary.mean() - replay.mean()) <= fraction * gain[key].mean()
            and (
                np.abs(primary - replay)[positive] <= fraction * gain[key][positive]
            ).all()
        )
    acceptance = json.loads((out / "acceptance.json").read_text())
    assert acceptance["checks"] == checks and acceptance["passed"] == all(
        checks.values()
    )
    return dict(
        verification_passed=True,
        artifact_files=len(manifest),
        metric_rows=len(table),
        daily_rows=len(daily),
        prediction_days=n - h,
        fit_days=h,
        metric_max_difference=metric_difference,
        daily_mean_max_difference_mm=csv_difference,
        raw_residual_summary_max_difference=raw_difference,
        manifest_sha256=sha(out / "artifact_manifest.json"),
        verifier_sha256=sha(__file__),
        optimizer_updates=900,
        new_training_or_native_calls=0,
        final_stage_started=False,
        effectiveness_passed=all(checks.values()),
        acceptance_checks_verified=len(checks),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = verify(args.run.resolve())
    if args.report:
        with args.report.open("x") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
