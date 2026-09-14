"""Read-only NumPy/model, index, scoring and event-order reproduction."""

import argparse
import json
import math
import traceback

import numpy as np
import pandas as pd
from scipy.special import ndtr
from scipy.stats import norm
import torch

from sequence_conditional.independent import numpy_forward
from .core import (
    ALPHAS,
    ARM,
    B,
    ROOT,
    Scaling,
    checkpoint_updates,
    guard,
    load_npz,
    predict,
    read_forcing,
    read_json,
    read_labels,
    reload_model,
    saved_source,
    sha,
    spec,
    utc,
    verify_implementation,
    verify_lock,
    write_json,
)


def independent_scores(y, mean, sigma=None):
    error = mean - y
    result = dict(
        mae=np.mean(abs(error), axis=0), rmse=np.sqrt(np.mean(error**2, axis=0))
    )
    if sigma is None:
        return result
    sigma = np.broadcast_to(sigma, mean.shape)
    z = (y - mean) / sigma
    density = np.exp(-z * z / 2) / math.sqrt(2 * math.pi)
    result["crps"] = np.mean(
        sigma * (z * (2 * ndtr(z) - 1) + 2 * density - 1 / math.sqrt(math.pi)), axis=0
    )
    for level in [80, 90, 95]:
        a = 1 - level / 100
        q = norm.ppf(1 - a / 2)
        lo, hi = mean - q * sigma, mean + q * sigma
        result[f"coverage{level}"] = np.mean((y >= lo) & (y <= hi), axis=0)
        result[f"width{level}"] = np.mean(hi - lo, axis=0)
        result[f"interval_score{level}"] = np.mean(
            hi - lo + 2 / a * np.maximum(lo - y, 0) + 2 / a * np.maximum(y - hi, 0),
            axis=0,
        )
    return result


def independent_gate(c, r, cfg):
    e = cfg["effect"]
    mean, probability = {}, {}
    for k in ["mae", "rmse"]:
        mean["average_" + k] = bool(
            c[k].mean() <= r[k].mean() * (1 - e["mean_relative_improvement"])
        )
    for k in ["crps", "interval_score90"]:
        probability["average_" + k] = bool(
            c[k].mean() <= r[k].mean() * (1 - e["probability_relative_improvement"])
        )
    probability["coverage90_average"] = bool(
        c["coverage90"].mean() >= e["coverage90_average_min"]
    )
    for j, p in enumerate(cfg["points"]):
        for k in ["mae", "rmse"]:
            mean[p + "_" + k] = bool(c[k][j] <= r[k][j] + e["point_mean_atol_mm"])
        for k in ["crps", "interval_score90"]:
            probability[p + "_" + k] = bool(
                c[k][j] <= r[k][j] * (1 + e["point_probability_max_regression"])
            )
        probability[p + "_coverage90"] = bool(
            c["coverage90"][j] >= e["coverage90_point_min"]
        )
    return dict(
        mean_pass=all(mean.values()),
        probability_pass=all(probability.values()),
        joint_pass=all(mean.values()) and all(probability.values()),
        mean_checks=mean,
        probability_checks=probability,
    )


def main(attempt):
    cfg = spec()
    root = ROOT / cfg["out"]
    out = root / attempt
    out.mkdir(exist_ok=False)
    receipt = dict(
        status="running",
        started_utc=utc(),
        values_checked=0,
        max_difference=0.0,
        checkpoint_count=0,
        new_fits=0,
        reused_fits=0,
        updates=0,
        ridge_replays=0,
    )

    def close(a, b, tol=1e-8):
        a, b = np.asarray(a, float), np.asarray(b, float)
        assert a.shape == b.shape, (a.shape, b.shape)
        assert np.isfinite(a).all() and np.isfinite(b).all()
        diff = float(np.max(abs(a - b))) if a.size else 0.0
        assert diff <= tol, (diff, tol)
        receipt["values_checked"] += int(a.size)
        receipt["max_difference"] = max(receipt["max_difference"], diff)

    try:
        verify_implementation(cfg)
        receipt["source_files"] = guard()
        verify_lock(root / "scoring_lock.json")
        torch.set_num_threads(1)
        torch.use_deterministic_algorithms(True)
        y = read_labels(ROOT / cfg["data"], 1461)
        _, dates = read_forcing(ROOT / cfg["data"], 1461)
        point_table = pd.read_csv(root / "analysis/metrics_by_point.csv").set_index(
            ["origin", "method", "point"]
        )
        summary_table = pd.read_csv(root / "analysis/phase_summary.csv").set_index(
            ["origin", "method"]
        )
        seed_table = pd.read_csv(root / "analysis/seed_summary.csv").set_index(
            ["origin", "method", "seed"]
        )
        daily = pd.read_csv(root / "analysis/daily_predictions.csv")
        recorded_gates = read_json(root / "analysis/effect_gates.json")
        recorded_pairing = read_json(root / "analysis/pairing.json")
        previous = None
        selected_rows = []
        all_metrics = {}
        for n, end in zip(cfg["origins"], cfg["ends"]):
            folder = root / f"origin_{n}"
            data = load_npz(root / f"implementation_verification/teacher_{n}.npz")
            assert np.array_equal(data["dates"], dates[:end])
            assert (
                int(data["teacher_fit_prefix"])
                == cfg["teacher_fit_prefixes"][str(n)]
                <= n
            )
            sc = Scaling(data["x"][:n], y[:n], cfg=cfg)
            assert sc.state == read_json(folder / "scaling.json")
            candidate = {}
            for key in cfg["lambdas"]:
                seed_means = []
                for s in range(3):
                    dest = folder / key / f"seed_{s}"
                    done = verify_lock(dest / "complete.json")
                    receipt["new_fits"] += done["new_fits"]
                    receipt["updates"] += done["updates"]
                    source = saved_source(cfg, n, key, s)
                    if source is not None:
                        meta = read_json(dest / "reused.json")
                        assert sha(source) == meta["source_sha256"]
                        paths = [(source, dest / "e400_mean.npy")]
                        receipt["reused_fits"] += 1
                    else:
                        paths = [
                            (dest / f"e{step}.pt", dest / f"e{step}_mean.npy")
                            for step in [0, 50, 100, 200, 400]
                        ]
                        trace = [
                            json.loads(line)
                            for line in (dest / "training.jsonl")
                            .read_text()
                            .splitlines()
                        ]
                        assert [r["step"] for r in trace] == list(range(1, 401))
                        close(
                            [r["total"] for r in trace],
                            [
                                r["data"] + cfg["lambdas"][key] * r["penalty"]
                                for r in trace
                            ],
                            1e-12,
                        )
                        assert all(np.isfinite(r["grad_norm"]) for r in trace)
                    for path, meanpath in paths:
                        model, scale, saved = reload_model(path, cfg)
                        assert scale.state == sc.state
                        assert saved["training_prefix"] == n and saved["arm"] == ARM
                        step = checkpoint_updates(saved)
                        expected_step = int(meanpath.stem.split("_")[0][1:])
                        assert step == expected_step
                        if key != "L0":
                            assert saved["regularization_lambda"] == cfg["lambdas"][key]
                        expected = np.load(meanpath)
                        close(
                            predict(model, scale, ARM, data["x"], data["mean"]),
                            expected,
                            0,
                        )
                        close(
                            numpy_forward(saved, data["x"], data["mean"]),
                            expected,
                            cfg["numerical"]["independent_forward_atol_mm"],
                        )
                        if step == 0:
                            close(expected, data["mean"], 0)
                        receipt["checkpoint_count"] += 1
                    seed_means.append(np.load(dest / "e400_mean.npy"))
                candidate[key] = np.stack(seed_means)
            for k, w in ALPHAS.items():
                candidate[k] = data["mean"][None] + w * (
                    candidate["L0"] - data["mean"][None]
                )
            ridge = load_npz(folder / "ridge.npz")
            z = (data["x"][:n] - sc.x_mean) / sc.x_std
            target = (y[:n] - sc.y0) / sc.unit
            xbar, ybar = z.mean(0), target.mean(0)
            u, d, vh = np.linalg.svd(z - xbar, full_matrices=False)
            rhs = np.einsum("ni,nj->ij", u, target - ybar, optimize=False)
            coef = np.einsum(
                "ik,kj->ij", vh.T, (d / (d * d + 1))[:, None] * rhs, optimize=False
            )
            intercept = ybar - np.einsum("i,ij->j", xbar, coef, optimize=False)
            close(coef, ridge["coef"], 1e-7)
            allz = (data["x"] - sc.x_mean) / sc.x_std
            rr = sc.y0 + sc.unit * (
                np.einsum("ni,ij->nj", allz, coef, optimize=False) + intercept
            )
            receipt["ridge_replays"] += 1
            means_all, sigma_all, seeds_all = {}, {}, {}
            for phase in ["alpha", "lambda"]:
                verify_lock(folder / f"{phase}_candidate_lock.json")
                verify_lock(folder / f"{phase}_issue_lock.json")
                cm = load_npz(folder / f"{phase}_candidate_means.npz")
                cs = load_npz(folder / f"{phase}_candidate_seeds.npz")
                for key in cm:
                    if key in candidate:
                        close(cs[key], candidate[key], 1e-9)
                        close(cm[key], candidate[key].mean(0), 1e-9)
                    elif key == B:
                        close(cm[key], data["mean"], 0)
                    elif key == "RR_COND":
                        close(cm[key], rr, 1e-7)
                    else:
                        close(
                            cm[key][n:],
                            y[n - 1]
                            + np.arange(1, end - n + 1)[:, None]
                            * (y[n - 1] - y[n - 2]),
                            0,
                        )
                if previous is None:
                    continue
                record = read_json(folder / f"{phase}_selection.json")
                prior = load_npz(
                    root / f"origin_{previous}/{phase}_candidate_means.npz"
                )
                order = cfg["selection"][phase + "_order"]
                hist = {
                    k: independent_scores(
                        y[previous : previous + 90], prior[k][previous : previous + 90]
                    )
                    for k in prior
                }
                remaining = list(order)
                for metric in ["mae", "rmse"]:
                    best = min(hist[k][metric].mean() for k in remaining)
                    remaining = [
                        k for k in remaining if hist[k][metric].mean() <= best + 1e-12
                    ]
                chosen = remaining[0]
                assert record["selected"] == chosen
                assert record["selection_indices"] == [previous, previous + 90]
                assert record["calibration_indices"] == [previous + 90, previous + 180]
                assert previous + 180 <= n
                for k in prior:
                    for metric in ["mae", "rmse"]:
                        close(hist[k][metric].mean(), record["scores"][k][metric])
                means = load_npz(folder / f"{phase}_means.npz")
                sigmas = load_npz(folder / f"{phase}_sigmas.npz")
                errors = load_npz(folder / f"{phase}_calibration_errors.npz")
                seeds = load_npz(folder / f"{phase}_seeds.npz")
                alias = "ALPHA_SELECTED" if phase == "alpha" else "LAMBDA_SELECTED"
                close(means[alias], means[chosen], 0)
                close(seeds[alias], seeds[chosen], 0)
                for key in means:
                    parent = chosen if key == alias else key
                    e = (
                        prior[parent][previous + 90 : previous + 180]
                        - y[previous + 90 : previous + 180]
                    )
                    close(e, errors[key], 0)
                    close(np.maximum(np.sqrt(np.mean(e * e, 0)), 1e-6), sigmas[key], 0)
                selected_rows.append(dict(origin=n, phase=phase, selected=chosen))
                means_all.update(means)
                sigma_all.update(sigmas)
                seeds_all.update(seeds)
            if previous is not None:
                metrics = {
                    k: independent_scores(y[n:end], mu[n:end], sigma_all[k])
                    for k, mu in means_all.items()
                }
                all_metrics[n] = metrics
                for key, mm in metrics.items():
                    for metric, value in mm.items():
                        close(
                            value,
                            np.array(
                                [
                                    point_table.loc[(n, key, p), metric]
                                    for p in cfg["points"]
                                ]
                            ),
                        )
                        close(value.mean(), summary_table.loc[(n, key), metric])
                    assert (
                        independent_gate(mm, metrics[B], cfg)
                        == recorded_gates[str(n)][key]
                    )
                    for s in range(3):
                        ss = independent_scores(
                            y[n:end], seeds_all[key][s, n:end], sigma_all[key]
                        )
                        for metric, value in ss.items():
                            close(value.mean(), seed_table.loc[(n, key, s), metric])
                    part = daily[(daily.origin == n) & (daily.method == key)]
                    assert len(part) == 293 * 4
                    close(part["mean"].to_numpy(), means_all[key][n:end].ravel())
                    close(part["observed"].to_numpy(), y[n:end].ravel())
                    close(
                        part["sigma"].to_numpy(),
                        np.broadcast_to(sigma_all[key], (293, 4)).ravel(),
                    )
                    assert list(part.date) == list(np.repeat(dates[n:end], 4))
                    close(part.distance.to_numpy(), np.repeat(np.arange(1, 294), 4), 0)
                for pair in [p for p in recorded_pairing if p["origin"] == n]:
                    c, r = pair["candidate"], pair["reference"]
                    gate = independent_gate(metrics[c], metrics[r], cfg)
                    assert all(pair[k] == v for k, v in gate.items())
                    flags = []
                    for s in range(3):
                        cm = independent_scores(y[n:end], seeds_all[c][s, n:end])
                        rm = independent_scores(y[n:end], seeds_all[r][s, n:end])
                        flags.append(
                            all(cm[k].mean() < rm[k].mean() for k in ["mae", "rmse"])
                        )
                    assert pair["seed_flags"] == flags and pair[
                        "seed_both_improve"
                    ] == sum(flags)
            previous = n
            print(
                f"verified origin {n}: {receipt['checkpoint_count']} checkpoints",
                flush=True,
            )
        events = [
            json.loads(s) for s in (root / "events.jsonl").read_text().splitlines()
        ]
        outer = [
            i
            for i, r in enumerate(events)
            if r["event"] == "label_prefix_read" and r["rows"] == 1461
        ]
        assert len(outer) == 1
        for n in cfg["origins"]:
            for phase in ["alpha", "lambda"]:
                issue = [
                    i
                    for i, r in enumerate(events)
                    if r["event"] == "trajectory_issued"
                    and r["origin"] == n
                    and r["phase"] == phase
                ]
                assert len(issue) == 1 and issue[0] < outer[0]
        for r in events[: outer[0]]:
            if r["event"] == "label_prefix_read":
                assert r["rows"] in cfg["origins"] and r["rows"] <= 1168
        assert sum(r["event"] == "fit_started" for r in events) == 42
        assert sum(r["event"] == "fit_completed" for r in events) == 42
        assert sum(r["event"] == "model_reused" for r in events) == 18
        assert sum(r["event"] == "ridge_fitted" for r in events) == 5
        assert (
            receipt["new_fits"] == 42
            and receipt["updates"] == 16800
            and receipt["reused_fits"] == 18
        )
        assert receipt["checkpoint_count"] == 228
        assert (
            len(point_table) == 224
            and len(summary_table) == 56
            and len(seed_table) == 168
        )
        assert len(daily) == 65632
        receipt.update(
            status="passed",
            completed_utc=utc(),
            events_checked=len(events),
            selected=selected_rows,
            point_rows=224,
            summary_rows=56,
            seed_rows=168,
            daily_rows=len(daily),
            limitations=[
                "overlapping windows and previously exposed data",
                "selection horizon90 vs test293",
                "972 uses frozen792 B+ teacher",
                "90 adjacent errors are dependent",
                "optimizer nonconvergence flags retained",
                "given future forcing, not operational forecasting",
            ],
        )
        write_json(out / "receipt.json", receipt)
        write_json(
            out / "manifest.json",
            {
                "files": {
                    str(p.relative_to(ROOT)): sha(p)
                    for p in sorted(root.rglob("*"))
                    if p.is_file() and out not in p.parents and p.suffix not in [".log"]
                }
            },
        )
    except Exception:
        receipt.update(
            status="failed", traceback=traceback.format_exc(), completed_utc=utc()
        )
        write_json(out / "receipt.json", receipt)
        raise
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", default="verification_v1")
    main(parser.parse_args().attempt)
