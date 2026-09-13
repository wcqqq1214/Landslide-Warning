"""Read-only model reload, independent scoring/calibration and chronology checks."""

import argparse
import json
import math
import time

import numpy as np
import pandas as pd
from scipy.special import ndtr, ndtri
import torch

from .common import (
    ROOT,
    CALLS,
    load_spec,
    sha,
    save_json,
    array_sha,
    now,
    check_deadline,
)
from .data import load_cache, observations, query, training, ridge_features
from .models import Scaling, RidgeMean, predict_neural
from .train import load_group
from .physics import PhysicalBank, replay


def arrays(path):
    with np.load(path) as a:
        return {k: a[k].copy() for k in a.files}


def close(actual, expected, atol=1e-8):
    if np.shape(actual) != np.shape(expected):
        raise AssertionError("Different shapes")
    np.testing.assert_allclose(actual, expected, atol=atol, rtol=0, equal_nan=True)
    good = np.isfinite(actual) & np.isfinite(expected)
    return (
        float(np.max(abs(np.asarray(actual)[good] - np.asarray(expected)[good])))
        if good.any()
        else 0.0
    )


def independent_scores(pred, y, points, common=False):
    rows = []
    for h in range(1, 8):
        mask = pred["origins"] + (7 if common else h) <= len(y)
        ids = pred["origins"][mask] + h - 1
        for p, point in enumerate(points):
            mu, sd = pred["mean"][mask, h - 1, p], pred["sigma"][mask, h - 1, p]
            e = y[ids, p] - mu
            z = e / sd
            c = e * (2 * ndtr(z) - 1) + sd * (
                2 * np.exp(-z * z / 2) / math.sqrt(2 * math.pi) - 1 / math.sqrt(math.pi)
            )
            row = dict(
                horizon=h,
                point=point,
                n=len(ids),
                mae=np.mean(abs(e)),
                rmse=np.sqrt(np.mean(e * e)),
                crps=np.mean(c),
            )
            for level in (0.8, 0.9, 0.95):
                q = ndtri((1 + level) / 2)
                lo = mu - q * sd
                hi = mu + q * sd
                width = hi - lo
                penalty = np.where(
                    y[ids, p] < lo,
                    lo - y[ids, p],
                    np.where(y[ids, p] > hi, y[ids, p] - hi, 0),
                )
                row[f"coverage{round(level * 100)}"] = np.mean(
                    (y[ids, p] >= lo) & (y[ids, p] <= hi)
                )
                row[f"width{round(level * 100)}"] = np.mean(width)
                row[f"interval_score{round(level * 100)}"] = np.mean(
                    width + 2 * penalty / (1 - level)
                )
            rows.append(row)
    return pd.DataFrame(rows)


def verify_scale(path, pred, spec, root, y):
    phase = path.relative_to(root).parts[0]
    start, end = spec["stages"][phase]
    name = path.stem
    source = json.loads(path.with_name(name + "_calibration.json").read_text())[
        "initialization"
    ]
    prior_phase = {
        "inner": None,
        "development": "inner",
        "later_exploratory": "development",
    }[phase]
    prior = None
    if prior_phase and (root / prior_phase / (name + ".npz")).exists():
        prior = arrays(root / prior_phase / (name + ".npz"))
    maximum = 0.0
    for k, s in enumerate(source):
        hist = []
        for n in s["drift_origins"]:
            assert n + k < start
            hist.append(y[n + k] - (y[n - 1] + (k + 1) * (y[n - 1] - y[n - 2])))
        for n in s["model_origins"]:
            assert prior is not None and n + k < start
            j = int(np.flatnonzero(prior["origins"] == n)[0])
            hist.append(y[n + k] - prior["mean"][j, k])
        assert len(hist) == 90
        for i, n in enumerate(pred["origins"]):
            if n + k >= end:
                continue
            # Build from the issued-origin cutoff, independently of update-loop bookkeeping.
            mature = np.arange(start, n - k)
            past = [y[o + k] - pred["mean"][o - start, k] for o in mature]
            pool = (hist + past)[-90:]
            expected = np.maximum(
                np.sqrt(np.sum(np.square(pool), axis=0) / len(pool)), 1e-6
            )
            maximum = max(maximum, close(pred["sigma"][i, k], expected, 1e-10))
    for i, n in enumerate(pred["origins"]):
        valid = min(7, end - n)
        assert str(pred["locks"][i]) == array_sha(
            np.stack([pred["mean"][i, :valid], pred["sigma"][i, :valid]])
        )
    return maximum


def verify_feedback(spec, phase, root, cache, y):
    start, end = spec["stages"][phase]
    base = arrays(
        ROOT
        / "results/ootang_rolling_v3/20260913/c8_online"
        / phase
        / "C8_ONLINE_DATA.npz"
    )["mean"][:, :7]
    anchor = query(cache, np.arange(start, end))["anchor"]
    max_mu = 0.0
    updates = 0
    for name, D in [("C16_CORE_RULES", 1), ("C16_PHYS_RULES", 2)]:
        a = arrays(root / phase / (name + "_feedback_state.npz"))
        pred = arrays(root / phase / (name + ".npz"))
        G = np.broadcast_to(np.eye(D), (7, 4, D, D)).copy()
        rhs = np.zeros((7, 4, D))
        features = np.zeros_like(a["features"])
        for i, n in enumerate(pred["origins"]):
            valid = min(7, end - n)
            if i:
                features[i, :valid, :, 0] = (y[n - 1] - base[i - 1, 0]) / a[
                    "core_unit"
                ][:valid]
                if D == 2:
                    features[i, :valid, :, 1] = (y[n - 1] - anchor[i - 1, 0]) / a[
                        "physical_unit"
                    ]
            beta = np.zeros((7, 4, D))
            for k in range(7):
                for p in range(4):
                    beta[k, p] = np.linalg.solve(G[k, p], rhs[k, p])
            close(beta, a["beta"][i], 1e-8)
            mu = base[i, :valid] + a["response_unit"][:valid] * np.sum(
                beta[:valid] * features[i, :valid], axis=2
            )
            max_mu = max(max_mu, close(mu, pred["mean"][i, :valid]))
            for k in range(7):
                j = i - k
                if j < 1:
                    continue
                x = features[j, k]
                r = (y[n] - base[j, k]) / a["response_unit"][k]
                for p in range(4):
                    G[k, p] += np.outer(x[p], x[p])
                    rhs[k, p] += x[p] * r[p]
                updates += 4
        close(features, a["features"], 1e-10)
        close(G, a["gram"], 1e-8)
        close(rhs, a["rhs"], 1e-8)
    return dict(max_mean_difference_mm=max_mu, independent_updates=updates)


def verify_models(spec, root, cache):
    internal = json.loads((root / "internal_selection.json").read_text())
    result = []
    for phase, (start, end) in spec["stages"].items():
        check_deadline(spec)
        tr = training(cache, spec, start)
        data = query(cache, np.arange(start, end))
        expected = Scaling(tr)
        saved = Scaling(state=json.loads((root / phase / "scaling.json").read_text()))
        for k in expected.state:
            close(getattr(saved, k), getattr(expected, k), 1e-12)
        tq = arrays(root / phase / "training_queries.npz")
        assert (tq["target_last"] < start).all() and (
            tq["teacher"] <= tq["origins"]
        ).all()
        for name, step in internal["checkpoints"].items():
            path = root / phase / "training" / name
            if not (root / phase / (name + ".npz")).exists():
                continue
            models, scale = load_group(path, name, spec, step)
            seeds, detail = predict_neural(models, name, scale, data, spec)
            pred = arrays(root / phase / (name + ".npz"))
            mu = seeds.mean(0)
            mu[data["origins"][:, None] + np.arange(7) >= end] = np.nan
            delta = close(mu, pred["mean"])
            if phase != "inner":
                close(seeds, arrays(root / phase / (name + "_seed_means.npz"))["mean"])
                if detail:
                    state = arrays(root / phase / (name + "_states.npz"))
                    close(np.stack([d["states"] for d in detail]), state["states"])
                    close(np.stack([d["initial"] for d in detail]), state["initial"])
            fitting, _ = predict_neural(models, name, scale, tr, spec)
            close(
                fitting.mean(0),
                arrays(root / phase / "fitting" / (name + ".npz"))["mean"],
            )
            result.append(
                dict(
                    phase=phase,
                    model=name,
                    reloaded_seeds=3,
                    mean_difference_mm=delta,
                    parameters=sum(p.numel() for p in models[0].parameters()),
                )
            )
        for name in ("RR_DIRECT", "RR_BRES"):
            model = RidgeMean(
                json.loads((root / phase / (name + "_model.json")).read_text())
            )
            x = ridge_features(tr)
            z = (x - model.feature_mean) / model.feature_std
            N = len(z)
            for h in range(7):
                for p in range(4):
                    A = np.column_stack([np.ones(N), z[:, h, p]])
                    penalty = np.sqrt(N * model.state["alpha"]) * np.eye(17)
                    penalty[0, 0] = 0
                    base = (
                        tr["anchor"][:, h, p]
                        if name == "RR_BRES"
                        else tr["last_y"][:, p]
                    )
                    target = (tr["target"][:, h, p] - base) / model.target_unit[h, p]
                    beta = np.linalg.lstsq(
                        np.vstack([A, penalty]), np.r_[target, np.zeros(17)], rcond=None
                    )[0]
                    close(beta, model.beta[h, p], 1e-8)
            pred = arrays(root / phase / (name + ".npz"))
            mu = model.predict(data)
            mu[data["origins"][:, None] + np.arange(7) >= end] = np.nan
            result.append(
                dict(
                    phase=phase,
                    model=name,
                    mean_difference_mm=close(mu, pred["mean"]),
                    parameters=476,
                )
            )
    return result


def verify_physical_cache(spec, cache, y, forcing):
    # Stratified boundaries fixed independently of prediction scores.
    origins = [
        252,
        253,
        300,
        341,
        342,
        343,
        431,
        432,
        433,
        611,
        612,
        613,
        791,
        792,
        793,
        1167,
        1168,
        1169,
        1454,
        1460,
    ]
    bank = PhysicalBank(forcing, y[0], spec["teacher_prefixes"])
    maximum = 0.0
    daily = 0.0
    for n in origins:
        q, _, b, s, packed, _ = bank.forecast(n)
        a = query(cache, [n])
        assert a["teacher"][0] == q
        maximum = max(
            maximum, close(a["anchor"][0], y[n - 1] + b[n : n + 7] - b[n - 1])
        )
        close(a["state"][0], packed[n - 1 : n + 7])
        exact, _ = replay(
            packed[n - 1],
            packed[n : n + 7, 20:],
            a["force"][0],
            a["elastic"][0],
            a["coeff"][0],
        )
        daily = max(daily, close(exact, packed[n : n + 7]))
        assert str(a["history_sha"][0]) == array_sha(y[:n])
        old = bank.forcing
        changed = forcing.copy()
        changed[n:] = [777, 199]
        bank.forcing = changed
        alternate = bank.forecast(n)
        bank.forcing = old
        close(alternate[2][: n + 7], b[: n + 7], 0)
    return dict(
        origins=origins,
        max_anchor_difference_mm=maximum,
        max_independent_daily_state_difference=daily,
        future_driver_perturbation_passed=True,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    spec = load_spec(args.config)
    check_deadline(spec)
    torch.set_num_threads(1)
    root = ROOT / spec["output_root"]
    out = root / "verification"
    out.mkdir(exist_ok=False)
    started = now()
    t = time.monotonic()
    result = dict(passed=False, start_utc=started)
    try:
        cache = load_cache(spec)
        y, forcing, _ = observations(spec)
        hashes = 0
        for path in [
            root / "implementation_lock.json",
            *root.glob("*/artifact_manifest.json"),
        ]:
            for name, value in json.loads(path.read_text()).items():
                source = (
                    ROOT / name
                    if path.name == "implementation_lock.json"
                    else path.parent / name
                )
                assert sha(source) == value
                hashes += 1
        pred_paths = []
        score_count = 0
        maxscore = 0.0
        maxscale = 0.0
        locks = 0
        for phase, (start, end) in spec["stages"].items():
            for path in (root / phase).rglob("*.npz"):
                if not path.with_name(path.stem + "_calibration.json").exists():
                    continue
                pred = arrays(path)
                assert np.array_equal(pred["origins"], np.arange(start, end))
                assert np.array_equal(
                    pred["teacher_prefixes"], cache["teacher"][start - 252 : end - 252]
                )
                maxscale = max(maxscale, verify_scale(path, pred, spec, root, y))
                locks += len(pred["origins"])
                pred_paths.append(str(path.relative_to(root)))
            for table in (root / phase).rglob("metrics_by_point_horizon*.csv"):
                frame = pd.read_csv(table)
                common = "_common" in table.stem
                for name, part in frame.groupby("model", sort=False):
                    path = table.parent / (name + ".npz")
                    if not path.exists():
                        path = root / phase / (name + ".npz")
                    calc = independent_scores(
                        arrays(path), y[:end], spec["points"], common
                    )
                    expected = part.drop(columns="model").reset_index(drop=True)
                    assert np.array_equal(
                        calc[["horizon", "point", "n"]],
                        expected[["horizon", "point", "n"]],
                    )
                    for c in calc.columns.difference(["horizon", "point"]):
                        maxscore = max(
                            maxscore,
                            close(calc[c].to_numpy(), expected[c].to_numpy(), 1e-9),
                        )
                        score_count += len(calc)
                summary = pd.read_csv(
                    table.with_name(
                        table.name.replace(
                            "metrics_by_point_horizon", "summary_by_horizon"
                        )
                    )
                )
                for _, r in summary.iterrows():
                    p = frame[(frame.model == r.model) & (frame.horizon == r.horizon)]
                    assert r.n_per_point == p.n.iloc[0]
                    for c in p.columns.difference(["model", "point", "horizon", "n"]):
                        close(np.array(r[c]), np.array(p[c].mean()), 1e-9)
                    close(
                        np.array(r.pooled_rmse),
                        np.array(np.sqrt(np.mean(p.rmse**2))),
                        1e-9,
                    )
        result.update(
            verified_hashes=hashes,
            prediction_files=pred_paths,
            forecast_locks=locks,
            score_cells=score_count,
            max_score_difference_mm=maxscore,
            max_scale_difference_mm=maxscale,
            models=verify_models(spec, root, cache),
            feedback={
                p: verify_feedback(spec, p, root, cache, y)
                for p in ("development", "later_exploratory")
            },
            physical_cache=verify_physical_cache(spec, cache, y, forcing),
        )
        events = [
            json.loads(x) for x in (root / "events.jsonl").read_text().splitlines()
        ]
        select = next(e for e in events if e["event"] == "internal_selection_locked")
        dev = next(
            e
            for e in events
            if e["event"] == "all_predictions_locked" and e["phase"] == "development"
        )
        later = next(
            e
            for e in events
            if e["event"] == "started" and e["phase"] == "later_exploratory"
        )
        assert select["time_utc"] < dev["time_utc"] < later["time_utc"]
        registry = json.loads((root / "fit_registry.json").read_text())
        assert len(registry) <= 36 and all(r["updates"] <= 400 for r in registry)
        result.update(
            passed=True,
            fit_count=len(registry),
            training_updates=sum(r["updates"] for r in registry),
            no_new_training=True,
            source_and_selection_chronology_passed=True,
        )
    except Exception as exc:
        result["error"] = repr(exc)
        raise
    finally:
        result.update(
            end_utc=now(), elapsed_seconds=time.monotonic() - t, calls=CALLS.copy()
        )
        save_json(out / "receipt.json", result)
        print(
            json.dumps(
                {
                    k: v
                    for k, v in result.items()
                    if k not in ("models", "prediction_files", "feedback")
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
