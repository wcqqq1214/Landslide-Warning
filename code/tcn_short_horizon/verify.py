"""Independent saved-artifact checks; no optimizer, retraining, or selection edits."""

import argparse
import json
import time

import numpy as np
import pandas as pd
import torch

from short_horizon.common import ROOT, load_spec, save_json, sha, array_sha, now
from short_horizon.data import load_cache, training, query, observations
from short_horizon.models import RidgeMean
from short_horizon.verify import close, independent_scores, verify_scale
from short_horizon.evaluation import quality, selection
from .models import ARMS, Scaling, predict
from .run import arrays, load_group


def verify(spec, root):
    cache = load_cache(spec)
    y, _, _ = observations(spec)
    hashes = 0
    for p in [
        root / "implementation_lock.json",
        *root.glob("*/artifact_manifest.json"),
    ]:
        for name, digest in json.loads(p.read_text()).items():
            actual = (
                ROOT / name if p.name == "implementation_lock.json" else p.parent / name
            )
            assert sha(actual) == digest, ("changed", actual)
            hashes += 1
    internal = json.loads((root / "internal_selection.json").read_text())
    dev = json.loads((root / "selection.json").read_text())
    assert dev["internal_selection_sha256"] == sha(root / "internal_selection.json")
    assert internal["config_sha256"] == sha(ROOT / "config/ootang_tcn.v1_0.json")
    models_count, score_cells, locks = 0, 0, 0
    max_model, max_score, max_scale = 0.0, 0.0, 0.0
    control_differences = []
    for phase, (start, end) in spec["stages"].items():
        tr = training(cache, spec, start)
        data = query(cache, np.arange(start, end))
        current = Scaling(tr)
        stored = Scaling(state=json.loads((root / phase / "scaling.json").read_text()))
        for k in current.state:
            close(getattr(current, k), getattr(stored, k), 0)
        q = arrays(root / phase / "training_queries.npz")
        assert np.array_equal(q["origins"], tr["origins"])
        assert np.array_equal(q["teacher"], tr["teacher"])
        assert (q["target_last"] < start).all() and (q["teacher"] <= q["origins"]).all()
        for name in ARMS:
            steps = (
                spec["neural"]["checkpoints"]
                if phase == "inner"
                else [internal["step"]]
            )
            for step in steps:
                models, scale = load_group(root / phase / "training" / name, spec, step)
                means = predict(models, name, scale, data)
                directory = (
                    root / phase / (f"{name}_step_{step}" if phase == "inner" else "")
                )
                saved = arrays(
                    directory
                    / (
                        "seed_means.npz"
                        if phase == "inner"
                        else name + "_seed_means.npz"
                    )
                )["mean"]
                max_model = max(max_model, close(means, saved, 0))
                models_count += 3
                if step == internal["step"]:
                    pred = arrays(root / phase / (name + ".npz"))
                    combined = means.mean(0)
                    combined[data["origins"][:, None] + np.arange(7) >= end] = np.nan
                    max_model = max(max_model, close(combined, pred["mean"], 0))
                    fit = predict(models, name, scale, tr).mean(0)
                    close(
                        fit,
                        arrays(root / phase / "fitting" / (name + ".npz"))["mean"],
                        0,
                    )
        ridge = RidgeMean(
            json.loads((root / phase / "RR_DIRECT_model.json").read_text())
        )
        mu = ridge.predict(data)
        mu[data["origins"][:, None] + np.arange(7) >= end] = np.nan
        close(mu, arrays(root / phase / "RR_DIRECT.npz")["mean"], 0)
        for name in ("B_ANCHOR", "DRIFT1", "RR_DIRECT"):
            old = arrays(ROOT / spec["legacy_root"] / phase / (name + ".npz"))
            new = arrays(root / phase / (name + ".npz"))
            control_differences.append(
                dict(
                    phase=phase,
                    model=name,
                    mean=close(new["mean"], old["mean"], 1e-10),
                    sigma=close(new["sigma"], old["sigma"], 1e-10),
                )
            )
        for path in (root / phase).rglob("*_calibration.json"):
            path = path.with_name(path.name.replace("_calibration.json", ".npz"))
            pred = arrays(path)
            assert np.array_equal(pred["origins"], np.arange(start, end))
            assert np.array_equal(
                pred["teacher_prefixes"], cache["teacher"][start - 252 : end - 252]
            )
            max_scale = max(max_scale, verify_scale(path, pred, spec, root, y))
            issued = arrays(path.with_name(path.stem + "_issued.npz"))
            close(
                issued["mean"][np.isfinite(pred["mean"])],
                pred["mean"][np.isfinite(pred["mean"])],
                0,
            )
            close(
                issued["sigma"][np.isfinite(pred["sigma"])],
                pred["sigma"][np.isfinite(pred["sigma"])],
                0,
            )
            assert (
                np.isfinite(issued["mean"]).all() and np.isfinite(issued["sigma"]).all()
            )
            for i in range(len(issued["origins"])):
                assert issued["locks"][i] == array_sha(
                    np.stack([issued["mean"][i], issued["sigma"][i]])
                )
            locks += len(pred["origins"])
        for table in (root / phase).rglob("metrics_by_point_horizon*.csv"):
            frame = pd.read_csv(table)
            for name, part in frame.groupby("model", sort=False):
                path = table.parent / (name + ".npz")
                if not path.exists():
                    path = root / phase / (name + ".npz")
                calc = independent_scores(
                    arrays(path), y[:end], spec["points"], "_common" in table.stem
                )
                expected = part.drop(columns="model").reset_index(drop=True)
                assert np.array_equal(
                    calc[["horizon", "point", "n"]], expected[["horizon", "point", "n"]]
                )
                for c in calc.columns.difference(["horizon", "point"]):
                    max_score = max(
                        max_score,
                        close(calc[c].to_numpy(), expected[c].to_numpy(), 1e-9),
                    )
                    score_cells += len(calc)
            summary = pd.read_csv(
                table.with_name(
                    table.name.replace("metrics_by_point_horizon", "summary_by_horizon")
                )
            )
            for _, r in summary.iterrows():
                p = frame[(frame.model == r.model) & (frame.horizon == r.horizon)]
                assert r.n_per_point == p.n.iloc[0]
                for c in p.columns.difference(["model", "horizon", "point", "n"]):
                    close(np.array(r[c]), np.array(p[c].mean()), 1e-9)
                close(
                    np.array(r.pooled_rmse), np.array(np.sqrt(np.mean(p.rmse**2))), 1e-9
                )
    scores = []
    for step in spec["neural"]["checkpoints"]:
        qs = []
        for name in ARMS:
            summary = pd.read_csv(
                root / "inner" / f"{name}_step_{step}" / "summary_by_horizon_common.csv"
            )
            qs.append(quality(summary, name))
        scores.append((step, float(np.mean(qs))))
    minimum = min(q for _, q in scores)
    assert internal["step"] == min(s for s, q in scores if q <= minimum + 1e-8)
    for (step, q), saved in zip(scores, internal["candidates"]):
        assert step == saved["step"]
        close(np.array(q), np.array(saved["paired_score"]), 1e-8)
    metrics = pd.read_csv(root / "development/metrics_by_point_horizon.csv")
    summary = pd.read_csv(root / "development/summary_by_horizon.csv")
    recreated = selection(spec, metrics, summary)
    for expected, actual in zip(recreated, dev["by_horizon"]):
        for key in ("horizon", "mean_best", "probability_best", "recommended", "gates"):
            assert expected[key] == actual[key], (key, expected, actual)
        assert not any("C16" in name for name in actual["gates"])
    registry = json.loads((root / "fit_registry.json").read_text())
    assert len(registry) == 18
    assert all(r["status"] == "completed" for r in registry)
    assert sum(r["updates"] for r in registry) == 2400 + 12 * internal["step"] <= 7200
    for phase in spec["stages"]:
        for seed in spec["neural"]["seeds"]:
            rows = [r for r in registry if r["phase"] == phase and r["seed"] == seed]
            assert len(rows) == 2
            for key in (
                "initialization_sha256",
                "training_origins_sha256",
                "scaling_sha256",
                "batches_sha256",
                "updates",
                "parameter_count",
            ):
                assert rows[0][key] == rows[1][key], (phase, seed, key)
    events = [
        json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()
    ]
    inside = next(e for e in events if e["event"] == "internal_selection_locked")
    locked = next(e for e in events if e["event"] == "development_selection_locked")
    later = next(e for e in events if e["event"] == "later_started_with_selection_lock")
    assert inside["time_utc"] < locked["time_utc"] < later["time_utc"]
    assert inside["sha256"] == sha(root / "internal_selection.json")
    assert locked["sha256"] == later["selection_sha256"] == sha(root / "selection.json")
    return dict(
        passed=True,
        verified_hashes=hashes,
        checkpoint_models_reloaded=models_count,
        forecast_locks=locks,
        score_cells=score_cells,
        max_model_difference_mm=max_model,
        max_score_difference_mm=max_score,
        max_scale_difference_mm=max_scale,
        controls_vs_v4=control_differences,
        paired_initialization_batches_units_passed=True,
        selection_chronology_passed=True,
        fit_count=len(registry),
        updates=sum(r["updates"] for r in registry),
        new_optimizer_updates=0,
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    spec = load_spec(args.config)
    root = ROOT / spec["output_root"]
    out = ROOT / args.output
    if out.exists():
        raise FileExistsError("Keep existing verification receipt")
    torch.set_num_threads(1)
    start = time.monotonic()
    began = now()
    result = dict(passed=False)
    try:
        result.update(verify(spec, root))
    except Exception as exc:
        result["error"] = repr(exc)
        raise
    finally:
        result.update(
            start_utc=began, end_utc=now(), elapsed_seconds=time.monotonic() - start
        )
        save_json(out, result)
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
