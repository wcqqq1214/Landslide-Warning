"""Independent C8 batch regressions, causal feature reconstruction and scoring."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

from verify_ootang_empirical import decision_checks
from verify_ootang_rolling import independent_metrics

ROOT = Path(__file__).resolve().parents[1]
POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def array_sha(x):
    x = np.ascontiguousarray(x)
    return hashlib.sha256(
        str((x.shape, str(x.dtype))).encode() + x.tobytes()
    ).hexdigest()


def arrays(p):
    with np.load(p, allow_pickle=False) as z:
        return {k: z[k].copy() for k in z.files}


def features_at(y, physics, n, H):
    prefix = max(p for p in physics if p <= n)
    b = physics[prefix]
    h = np.arange(1, H + 1)[:, None]
    drift = {w: y[n - 1] + h * (y[n - 1] - y[n - 1 - w]) / w for w in (1, 3, 7, 14, 30)}
    anchor = y[n - 1] + (b[n : n + H] - b[n - 1])
    trend = anchor + h * ((y[n - 1] - y[n - 15]) - (b[n - 1] - b[n - 15])) / 14
    raw = np.stack(
        [
            drift[1] - drift[3],
            drift[1] - drift[7],
            drift[7] - drift[14],
            drift[14] - drift[30],
            anchor - y[n - 1] - h * (b[n - 1] - b[n - 2]),
            trend - drift[14],
        ],
        axis=-1,
    )
    return raw, drift[1], prefix


def verify_calibration(pred, labels, start, end, prob, state):
    H = pred["mean"].shape[1]
    logs = np.zeros((H, 4))
    issued = np.full_like(pred["sigma"], np.nan)
    hits = np.zeros((H, 4), int)
    counts = np.zeros(H, int)
    minimum = maximum = 0.0
    error_max = 0.0
    cfg = prob["feedback"]
    q = NormalDist().inv_cdf((1 + cfg["target_coverage"]) / 2)
    for i, n in enumerate(range(start, end)):
        for k in range(min(H, end - n)):
            stop = max(0, i - k)
            begin = max(0, stop - prob["window"])
            old = np.arange(begin, stop)
            z = (labels[start + old + k] - pred["mean"][old, k]) / pred["raw_sigma"][
                old, k
            ]
            factor = np.sqrt(
                (prob["prior_sum_squares"] + (z * z).sum(axis=0))
                / (prob["prior_count"] + len(old))
            ) * np.exp(logs[k])
            issued[i, k] = np.maximum(
                prob["sigma_floor_mm"], pred["raw_sigma"][i, k] * factor
            )
            np.testing.assert_allclose(
                pred["feedback_log_scale"][i, k], logs[k], atol=1e-12, rtol=1e-12
            )
            np.testing.assert_allclose(
                pred["calibration_factor"][i, k], factor, atol=1e-12, rtol=1e-12
            )
            np.testing.assert_allclose(
                pred["sigma"][i, k], issued[i, k], atol=1e-10, rtol=1e-12
            )
            error_max = max(
                error_max, float(abs(pred["sigma"][i, k] - issued[i, k]).max())
            )
        for k in range(min(H, i + 1)):
            j = i - k
            miss = abs(labels[n] - pred["mean"][j, k]) > q * issued[j, k]
            proposed = logs[k] + cfg["rate"] * (
                miss.astype(float) - (1 - cfg["target_coverage"])
            )
            bound = cfg["log_scale_bound"]
            hits[k] += abs(proposed) >= bound
            logs[k] = np.minimum(bound, np.maximum(-bound, proposed))
            counts[k] += 1
            minimum = min(minimum, float(logs.min()))
            maximum = max(maximum, float(logs.max()))
    np.testing.assert_allclose(state["log_scale_final"], logs, atol=1e-12, rtol=1e-12)
    np.testing.assert_array_equal(state["bound_hits"], hits)
    np.testing.assert_array_equal(state["updates_per_horizon"], counts)
    np.testing.assert_allclose(
        [state["log_scale_min"], state["log_scale_max"]],
        [minimum, maximum],
        atol=1e-12,
        rtol=1e-12,
    )
    return error_max


def batch_coefficients(train, issued, model, saved, labels, start, end):
    d = model["dimensions"]
    H = issued["features"].shape[1]
    N = end - start
    sx = np.array(model["feature_scale"])
    sy = np.array(model["target_scale"])
    beta0 = np.array(model["beta"])
    X = train["features"][..., :d] / sx[None]
    z = (train["target"] - train["base"]) / sy[None]
    G = np.einsum("nhpi,nhpj->hpij", X, X, optimize=False) + len(X) * model[
        "alpha"
    ] * np.eye(d)
    b = np.einsum("nhpi,nhp->hpi", X, z, optimize=False)
    np.testing.assert_array_equal(saved["beta_issued"][0], beta0)
    np.testing.assert_allclose(saved["initial_gram"], G, rtol=1e-12, atol=1e-10)
    np.testing.assert_allclose(saved["initial_rhs"], b, rtol=1e-12, atol=1e-10)
    beta = np.empty_like(saved["beta_issued"])
    coef_max = 0.0
    svd_max = 0.0
    svds = 0
    for k in range(H):
        count = N - k
        x = issued["features"][:count, k, :, :d] / sx[k]
        target = (labels[start + k : end] - issued["base"][:count, k]) / sy[k]
        outer = x[:, :, :, None] * x[:, :, None, :]
        xy = x * target[:, :, None]
        gram_prefix = np.concatenate(
            [np.zeros_like(outer[:1]), np.cumsum(outer, axis=0)]
        )
        rhs_prefix = np.concatenate([np.zeros_like(xy[:1]), np.cumsum(xy, axis=0)])
        mature = np.maximum(0, np.arange(N) - k)
        beta[:, k] = np.linalg.solve(
            G[k] + gram_prefix[mature], (b[k] + rhs_prefix[mature])[..., None]
        )[..., 0]
        delta = float(abs(beta[:, k] - saved["beta_issued"][:, k]).max())
        coef_max = max(coef_max, delta)
        np.testing.assert_allclose(
            beta[:, k], saved["beta_issued"][:, k], rtol=5e-11, atol=1e-8
        )
        np.testing.assert_allclose(
            saved["final_gram"][k], G[k] + gram_prefix[-1], rtol=5e-11, atol=1e-8
        )
        np.testing.assert_allclose(
            saved["final_rhs"][k], b[k] + rhs_prefix[-1], rtol=5e-11, atol=1e-8
        )
        np.testing.assert_allclose(
            saved["final_beta"][k],
            np.linalg.solve(G[k] + gram_prefix[-1], (b[k] + rhs_prefix[-1])[..., None])[
                ..., 0
            ],
            rtol=5e-11,
            atol=1e-8,
        )
        if saved["updates"][k] != count or saved["last_target"][k] != end - 1:
            raise ValueError("Online update count or final target differs")
        if k in (0, 6, 29):
            for i in sorted(set([0, N - 1] + list(range(0, N, 30)))):
                m = max(0, i - k)
                for p in range(4):
                    augmented = np.vstack(
                        [
                            X[:, k, p],
                            x[:m, p],
                            np.sqrt(len(X) * model["alpha"]) * np.eye(d),
                        ]
                    )
                    response = np.r_[z[:, k, p], target[:m, p], np.zeros(d)]
                    independent = np.linalg.lstsq(augmented, response, rcond=None)[0]
                    svd_max = max(
                        svd_max,
                        float(abs(independent - saved["beta_issued"][i, k, p]).max()),
                    )
                    svds += 1
                    np.testing.assert_allclose(
                        independent,
                        saved["beta_issued"][i, k, p],
                        rtol=5e-11,
                        atol=1e-8,
                    )
    norm = issued["features"][..., :d] / sx[None]
    mean = issued["base"] + np.sum(norm * beta, axis=-1) * sy[None]
    return mean, dict(
        max_batch_coefficient_difference=coef_max,
        max_augmented_svd_difference=svd_max,
        augmented_svd_checks=svds,
        point_updates=int(4 * saved["updates"].sum()),
    )


def verify(run):
    manifest = json.loads((run / "artifact_manifest.json").read_text())["files"]
    for name, wanted in manifest.items():
        if sha(run / name) != wanted:
            raise ValueError("Output changed: " + name)
    source = json.loads((run / "sources.json").read_text())["files"]
    for name, wanted in source.items():
        p = run / "sources" / name
        if not p.exists():
            p = ROOT / name
        if sha(p) != wanted:
            raise ValueError("Source changed: " + name)
    spec = json.loads(
        (run / "sources/config/ootang_rolling_probability.v3_7.json").read_text()
    )
    if not spec["post_transfer_exposure"] or spec["original_single_transfer_reused"]:
        raise ValueError("Exposure limitation missing")
    labels = pd.read_csv(ROOT / spec["data"], usecols=[p + "/mm" for p in POINTS])[
        [p + "/mm" for p in POINTS]
    ].to_numpy(float)
    records = {}
    feature_data = {}
    states = {}
    coefficients = {}
    max_mean = max_feature = max_cal = max_score = 0.0
    score_cells = 0
    final = json.loads((run / "decision.json").read_text())
    total_updates = 0
    for phase, (start, end) in spec["stages"].items():
        ref = spec["sources"][phase]
        directory = run / phase
        H = spec["horizons"]
        N = end - start
        physics = {
            int(p.stem.split("_")[-1]): arrays(p)["mean"]
            for p in (ROOT / ref["physics_cache"]).glob("teacher_*.npz")
        }
        train = arrays(directory / "initial_training.npz")
        issued = arrays(directory / "issued_features.npz")
        feature_data[phase] = issued
        np.testing.assert_array_equal(
            train["origins"], np.arange(min(physics), start - H + 1)
        )
        contract = json.loads((directory / "initial_contract.json").read_text())
        if contract["training_rows"] != start or contract["label_sha256"] != array_sha(
            labels[:start]
        ):
            raise ValueError("Initial label prefix differs")
        for i, n in enumerate(train["origins"]):
            f, base, prefix = features_at(labels[:n], physics, int(n), H)
            max_feature = max(max_feature, float(abs(f - train["features"][i]).max()))
            np.testing.assert_allclose(f, train["features"][i], rtol=5e-11, atol=1e-8)
            np.testing.assert_array_equal(base, train["base"][i])
            np.testing.assert_array_equal(labels[n : n + H], train["target"][i])
            if prefix != train["teachers"][i]:
                raise ValueError("Historical teacher identity differs")
        for i, n in enumerate(range(start, end)):
            valid = min(H, end - n)
            f, base, prefix = features_at(labels[:n], physics, n, valid)
            max_feature = max(
                max_feature, float(abs(f - issued["features"][i, :valid]).max())
            )
            np.testing.assert_allclose(
                f, issued["features"][i, :valid], rtol=5e-11, atol=1e-8
            )
            np.testing.assert_array_equal(base, issued["base"][i, :valid])
            if prefix != start:
                raise ValueError("Current physical teacher changed within phase")
        records[phase] = {
            p.stem: arrays(p)
            for p in directory.glob("*.npz")
            if p.stem.startswith("C8_")
            or p.stem
            in [
                "B_RAW",
                "B_ANCHOR",
                "B_TREND14",
                "DRIFT1",
                "DRIFT3",
                "DRIFT7",
                "DRIFT14",
                "DRIFT30",
                "PERSIST",
                "EXPERT_INIT",
            ]
        }
        feedback = json.loads((directory / "feedback_state.json").read_text())
        states[phase] = {}
        for arm in ("FULL", "DATA"):
            model_path = Path(ref["training_path"]) / f"ridge_{arm}_a0.001.json"
            model = json.loads((run / "sources" / model_path).read_text())
            state = arrays(directory / f"online_state_{arm}.npz")
            states[phase][arm] = state
            d = model["dimensions"]
            np.testing.assert_array_equal(
                np.maximum(
                    np.sqrt(np.mean(train["features"][..., :d] ** 2, axis=0)), 1e-6
                ),
                model["feature_scale"],
            )
            np.testing.assert_array_equal(
                np.maximum(
                    np.sqrt(np.mean((train["target"] - train["base"]) ** 2, axis=0)),
                    spec["probability"]["sigma_floor_mm"],
                ),
                model["target_scale"],
            )
            mean, record = batch_coefficients(
                train, issued, model, state, labels, start, end
            )
            name = "C8_ONLINE_" + arm
            pred = records[phase][name]
            np.testing.assert_allclose(
                mean, pred["mean"], rtol=5e-11, atol=1e-8, equal_nan=True
            )
            max_mean = max(max_mean, float(np.nanmax(abs(mean - pred["mean"]))))
            coefficients[phase + "/" + arm] = record
            total_updates += record["point_updates"]
            old = arrays(
                ROOT / ref["current_run"] / ref["current_phase"] / f"C4_RIDGE_{arm}.npz"
            )
            np.testing.assert_array_equal(pred["raw_sigma"], old["raw_sigma"])
            np.testing.assert_array_equal(pred["mean"][0], old["mean"][0])
            max_cal = max(
                max_cal,
                verify_calibration(
                    pred, labels, start, end, spec["probability"], feedback[name]
                ),
            )
        rows = []
        for name, pred in records[phase].items():
            np.testing.assert_array_equal(pred["origins"], np.arange(start, end))
            if not name.startswith("C8_ONLINE"):
                original = (
                    "C4_RIDGE_" + name.rsplit("_", 1)[-1]
                    if name.startswith("C8_STATIC")
                    else name
                )
                old = arrays(
                    ROOT
                    / ref["current_run"]
                    / ref["current_phase"]
                    / (original + ".npz")
                )
                if old.keys() != pred.keys():
                    raise ValueError("Static distribution fields changed")
                for key in old:
                    np.testing.assert_array_equal(pred[key], old[key])
            for k in range(H):
                count = N - k
                y = labels[start + k : end]
                mu = pred["mean"][:count, k]
                sigma = pred["sigma"][:count, k]
                if name == "B_RAW":
                    vals = dict(
                        mae=abs(mu - y).mean(axis=0),
                        rmse=np.sqrt(((mu - y) ** 2).mean(axis=0)),
                    )
                else:
                    vals = independent_metrics(y, mu, sigma)
                for j, point in enumerate(POINTS):
                    rows.append(
                        dict(
                            model=name,
                            horizon=k + 1,
                            point=point,
                            n=count,
                            **{key: float(value[j]) for key, value in vals.items()},
                        )
                    )
        computed = pd.DataFrame(rows)
        summary_rows = []
        for (name, h), g in computed.groupby(["model", "horizon"], sort=False):
            row = dict(
                model=name,
                horizon=int(h),
                n_per_point=int(g.n.iloc[0]),
                **{
                    key: float(g[key].mean())
                    for key in computed
                    if key not in ("model", "horizon", "point", "n")
                },
            )
            row["pooled_rmse"] = float(np.sqrt(np.mean(g.rmse**2)))
            summary_rows.append(row)
        summary = pd.DataFrame(summary_rows)
        for frame, file, keys in [
            (computed, "metrics.csv", ["model", "horizon", "point"]),
            (summary, "summary.csv", ["model", "horizon"]),
        ]:
            a = frame.set_index(keys).sort_index()
            b = pd.read_csv(directory / file).set_index(keys).sort_index()
            np.testing.assert_allclose(
                a.to_numpy(),
                b[a.columns].to_numpy(),
                rtol=5e-11,
                atol=1e-8,
                equal_nan=True,
            )
            max_score = max(
                max_score, float(np.nanmax(abs(a.to_numpy() - b[a.columns].to_numpy())))
            )
            score_cells += a.size
        checks = decision_checks(computed, summary, spec["candidate"], spec)
        decision = json.loads((directory / "decision.json").read_text())
        if (
            checks != decision["checks"]
            or decision["passed"] != all(checks.values())
            or decision["passed_count"] != sum(checks.values())
            or decision != final["phases"][phase]
        ):
            raise ValueError("Effect decision differs")
    chain = ""
    pending = None
    next_origin = {}
    locks = 0
    for raw in (run / "events.jsonl").read_text().splitlines():
        e = json.loads(raw)
        if e["previous_sha256"] != chain:
            raise ValueError("Event chain differs")
        chain = hashlib.sha256(raw.encode()).hexdigest()
        phase = e["phase"]
        start, end = spec["stages"][phase]
        if e["kind"] == "phase_started":
            next_origin[phase] = start
        elif e["kind"] == "forecast_locked":
            n = e["origin"]
            i = n - start
            h = min(spec["horizons"], end - n)
            if pending is not None or next_origin[phase] != n:
                raise ValueError("Incorrect forecast order")
            if e["history_sha256"] != array_sha(labels[:n]) or e[
                "feature_sha256"
            ] != array_sha(feature_data[phase]["features"][i, :h]):
                raise ValueError("Issued history or features differ")
            for arm in ("FULL", "DATA"):
                if e["beta_sha256"][arm] != array_sha(
                    states[phase][arm]["beta_issued"][i]
                ):
                    raise ValueError("Issued coefficients differ")
            expected = {
                name: array_sha(
                    np.stack([pred[k][i, :h] for k in ("mean", "sigma", "raw_sigma")])
                )
                for name, pred in records[phase].items()
            }
            if expected != e["predictions"]:
                raise ValueError("Issued predictions differ")
            pending = (phase, n)
            locks += 1
        elif e["kind"] == "observation_released":
            if pending != (phase, e["index"]) or e["value_sha256"] != array_sha(
                labels[e["index"]]
            ):
                raise ValueError("Released observation order differs")
            pending = None
            next_origin[phase] += 1
        elif e["kind"] == "phase_completed":
            if pending is not None or next_origin[phase] != end:
                raise ValueError("Incomplete phase")
        else:
            raise ValueError("Unknown event")
    if (
        pending is not None
        or locks != sum(e - s for s, e in spec["stages"].values())
        or total_updates != final["online_point_solves"]
    ):
        raise ValueError("Incomplete run or wrong update count")
    return dict(
        passed=True,
        checked_utc=datetime.now(timezone.utc).isoformat(),
        run=str(run),
        immutable_artifacts_checked=len(manifest),
        sources_checked=len(source),
        forecast_locks=locks,
        original_online_point_updates_verified=total_updates,
        coefficient_checks=coefficients,
        max_independent_feature_difference=max_feature,
        max_independent_mean_difference_mm=max_mean,
        max_calibration_difference_mm=max_cal,
        score_cells_checked=score_cells,
        max_csv_rounding_difference=max_score,
        static_distributions_and_raw_scales_unchanged=True,
        post_exposure=True,
        independent_transfer=False,
        new_training_updates=0,
        physical_calls=0,
        verifier_sha256=sha(__file__),
        dependency_sha256={
            name: sha(Path(__file__).with_name(name))
            for name in ("verify_ootang_empirical.py", "verify_ootang_rolling.py")
        },
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    result = verify(args.run)
    args.out.mkdir(parents=True)
    (args.out / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    for name in (
        "verify_ootang_online.py",
        "verify_ootang_empirical.py",
        "verify_ootang_rolling.py",
    ):
        (args.out / name).write_bytes(Path(__file__).with_name(name).read_bytes())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
