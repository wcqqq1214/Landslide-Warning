"""Independent Transformer attention / serial Mamba, metrics and issue-order audit."""

import argparse
import json
import math
import traceback

import numpy as np
import pandas as pd
import torch
from scipy.special import erf, ndtri

from .core import (
    ROOT,
    guard_sources,
    load_npz,
    read_json,
    read_labels,
    sha,
    spec,
    utc,
    write_json,
)


from .independent import numpy_forward
from .core import reload_model, predict


def independently_score(y, mu, sigma=None):
    err = mu - y
    answer = dict(mae=np.mean(abs(err), axis=0), rmse=np.sqrt(np.mean(err**2, axis=0)))
    if sigma is not None:
        sd = np.broadcast_to(sigma, mu.shape)
        z = (y - mu) / sd
        val = sd * (
            z * erf(z / math.sqrt(2))
            + math.sqrt(2 / math.pi) * np.exp(-z * z / 2)
            - 1 / math.sqrt(math.pi)
        )
        answer["crps"] = val.mean(0)
        for level in (80, 90, 95):
            alpha = 1 - level / 100
            q = ndtri(1 - alpha / 2)
            lo, hi = mu - q * sd, mu + q * sd
            answer[f"coverage{level}"] = ((y >= lo) & (y <= hi)).mean(0)
            answer[f"width{level}"] = (hi - lo).mean(0)
            penalty = (hi - lo) + 2 / alpha * (
                (lo - y) * (y < lo) + (y - hi) * (y > hi)
            )
            answer[f"interval_score{level}"] = penalty.mean(0)
    return answer


def main(attempt):
    cfg = spec()
    torch.set_num_threads(1)
    root = ROOT / cfg["out"]
    dest = root / f"verification_{attempt}"
    dest.mkdir(exist_ok=False)
    state = dict(
        status="running",
        time_utc=utc(),
        attempt=attempt,
        max_abs_difference=0.0,
        numerical_values_checked=0,
        model_checkpoints=0,
        formal_new_fits=0,
        optimizer_updates=0,
    )

    def close(actual, expected, tolerance=1e-8):
        a, b = np.asarray(actual, dtype=float), np.asarray(expected, dtype=float)
        assert a.shape == b.shape, (a.shape, b.shape)
        delta = float(np.max(abs(a - b))) if a.size else 0.0
        state["max_abs_difference"] = max(state["max_abs_difference"], delta)
        state["numerical_values_checked"] += a.size
        np.testing.assert_allclose(a, b, rtol=0, atol=tolerance)

    def verify_manifest(path):
        item = read_json(path)
        for name, digest in item["files"].items():
            assert sha(path.parent / name) == digest, (path, name)

    try:
        state["source_files_checked"] = guard_sources()
        impl = read_json(root / "implementation_lock.json")
        for name, digest in impl["files"].items():
            assert sha(ROOT / name) == digest, name
        # Final labels are read only now, after all original distribution locks exist.
        for phase in ("development", "final_exploratory"):
            verify_manifest(root / phase / "distribution_lock.json")
        y = read_labels(ROOT / cfg["data"], 1461)
        selected = read_json(root / "internal_selection.json")
        update = selected["selected_updates"]
        assert all(v in cfg["neural"]["checkpoints"] for v in update.values())
        steps = {a: update[f] for f, pair in cfg["families"].items() for a in pair}
        reuse = ROOT / cfg["reuse"]
        stage_means, stage_sigmas, seed_avgs = {}, {}, {}
        formal_fits, total_updates, train_seconds = 0, 0, 0.0
        q_values = {
            f: {str(k): [] for k in cfg["neural"]["checkpoints"]}
            for f in cfg["families"]
        }
        comparison_rows = []
        for phase, (n, end) in cfg["stages"].items():
            directory = root / phase
            verify_manifest(directory / "mean_lock.json")
            verify_manifest(directory / "scoring_lock.json")
            data = load_npz(reuse / "implementation_verification" / f"teacher_{n}.npz")
            scaler = read_json(directory / "scaling.json")
            close(scaler["x_mean"], data["x"][:n].mean(0))
            close(scaler["x_std"], np.maximum(data["x"][:n].std(0), 1e-6))
            unit = np.maximum((y[:n] - y[0]).std(0), 1)
            close(scaler["unit"], unit)
            b = load_npz(directory / "reused_predictions.npz")
            close(b["BPLUS_CONTINUOUS"], data["mean"][n:])
            close(
                b["DRIFT1"],
                y[n - 1] + np.arange(1, end - n + 1)[:, None] * (y[n - 1] - y[n - 2]),
            )
            # SVD solves the saved, fixed ridge objective independently of its normal equations.
            z = (data["x"][:n] - np.array(scaler["x_mean"])) / np.array(scaler["x_std"])
            xbar = z.mean(0)
            target = (y[:n] - y[0]) / unit
            u, s, vt = np.linalg.svd(z - xbar, full_matrices=False)
            coef = (vt.T * (s / (s * s + 1))) @ (u.T @ (target - target.mean(0)))
            intercept = target.mean(0) - xbar @ coef
            ridge = load_npz(reuse / phase / "ridge.npz")
            close(ridge["coef"], coef)
            zfull = (data["x"] - np.array(scaler["x_mean"])) / np.array(scaler["x_std"])
            rr = y[0] + unit * (zfull @ coef + intercept)
            close(b["RR_COND"], rr[n:])
            old_tcn = load_npz(reuse / phase / "ensemble_e100.npz")
            for m, values in old_tcn.items():
                close(b[m], values[n:], 0)
            generated = {}
            for arm in cfg["arms"]:
                for seed in cfg["neural"]["seeds"]:
                    d = directory / arm / f"seed_{seed}"
                    verify_manifest(d / "complete.json")
                    done = read_json(d / "complete.json")
                    formal_fits += 1
                    total_updates += done["updates"]
                    train_seconds += done["elapsed_seconds"]
                    records = [
                        json.loads(line)
                        for line in (d / "training.jsonl").read_text().splitlines()
                    ]
                    assert [r["step"] for r in records] == list(
                        range(1, done["updates"] + 1)
                    )
                    assert all(
                        np.isfinite(r["pre_update_loss"])
                        and np.isfinite(r["gradient_norm"])
                        for r in records
                    )
                    for checkpoint in sorted(d.glob("e*.pt")):
                        saved = torch.load(
                            checkpoint, map_location="cpu", weights_only=True
                        )
                        assert saved["training_prefix"] == n
                        assert saved["seed"] == seed and saved["arm"] == arm
                        mu = numpy_forward(saved, data["x"], data["mean"])
                        original = np.load(d / f"e{saved['updates']}_mean.npy")
                        close(
                            original,
                            mu,
                            cfg["numerical"]["independent_forward_atol_mm"],
                        )
                        model, scale, meta = reload_model(checkpoint, cfg)
                        close(
                            predict(model, scale, arm, data["x"], data["mean"]),
                            original,
                            0,
                        )
                        assert meta["config_sha256"] == sha(
                            ROOT / "config/ootang_sequence_conditional.v1_0.json"
                        )
                        f = read_json(d / f"e{saved['updates']}_fit.json")
                        close(
                            np.array(f["normalized_loss"]),
                            np.array(np.mean(((mu[:n] - y[:n]) / unit) ** 2)),
                        )
                        state["model_checkpoints"] += 1
                        generated[(arm, seed, saved["updates"])] = original
            # Same-family pairs have identical initialization for each seed/stage.
            for pair in cfg["families"].values():
                for seed in cfg["neural"]["seeds"]:
                    a0, b0 = [
                        torch.load(
                            directory / a / f"seed_{seed}/e0.pt", weights_only=True
                        )
                        for a in pair
                    ]
                    for k in a0["state_dict"]:
                        torch.testing.assert_close(
                            a0["state_dict"][k], b0["state_dict"][k], atol=0, rtol=0
                        )
                    close(
                        generated[(pair[0], seed, 0)],
                        np.broadcast_to(y[0], (end, 4)),
                        0,
                    )
                    close(generated[(pair[1], seed, 0)], data["mean"], 0)
            means = dict(b)
            ens = load_npz(directory / "ensemble_selected.npz")
            for arm in cfg["arms"]:
                expected = np.mean(
                    [generated[(arm, s, steps[arm])] for s in cfg["neural"]["seeds"]],
                    axis=0,
                )
                close(ens[arm], expected)
                means[arm] = ens[arm][n:]
            stage_means[phase] = means
            if phase == "internal":
                for step in cfg["neural"]["checkpoints"]:
                    checkpoint = load_npz(directory / f"ensemble_e{step}.npz")
                    for arm in cfg["arms"]:
                        expected = np.mean(
                            [generated[(arm, s, step)] for s in cfg["neural"]["seeds"]],
                            axis=0,
                        )
                        close(checkpoint[arm], expected)
                        family = next(
                            f for f, pair in cfg["families"].items() if arm in pair
                        )
                        q_values[family][str(step)].extend(
                            (
                                np.sqrt(
                                    np.mean(
                                        (expected[612:702] - y[612:702]) ** 2, axis=0
                                    )
                                )
                                / unit
                            ).tolist()
                        )
                for family, q in q_values.items():
                    quality = {k: float(np.mean(v)) for k, v in q.items()}
                    for k, v in quality.items():
                        close(np.array(selected["quality"][family][k]), np.array(v))
                    winner = min(
                        int(k)
                        for k, v in quality.items()
                        if v <= min(quality.values()) + 1e-12
                    )
                    assert winner == update[family]
                pool = {m: y[702:792] - means[m][90:] for m in cfg["methods"]}
                nextcal = read_json(directory / "next_calibration.json")
                for m, v in pool.items():
                    close(
                        nextcal["scales"][m],
                        np.maximum(np.sqrt(np.mean(v * v, axis=0)), 1e-6),
                    )
                np.savez_compressed(dest / "internal_calibration_errors.npz", **pool)
                continue
            previous = "internal" if phase == "development" else "development"
            cal = read_json(root / previous / "next_calibration.json")
            issue = load_npz(directory / "issued_distribution.npz")
            sigma = {m: np.array(cal["scales"][m]) for m in cfg["methods"]}
            stage_sigmas[phase] = sigma
            metrics = pd.read_csv(directory / "metrics_by_point.csv")
            summary = pd.read_csv(directory / "summary.csv").set_index("model")
            fit = pd.read_csv(directory / "fitting_by_point.csv")
            daily = pd.read_csv(directory / "daily_predictions.csv")
            seeds = pd.read_csv(directory / "seed_metrics.csv")
            assert len(daily) == len(cfg["methods"]) * 4 * (end - n)
            assert list(issue["dates"]) == list(data["dates"][n:])
            values = {}
            for m in cfg["methods"]:
                close(issue[m + "__mean"], means[m])
                close(issue[m + "__sigma"], sigma[m])
                s = independently_score(y[n:end], means[m], sigma[m])
                values[m] = s
                actual = metrics[metrics.model == m]
                assert tuple(actual.point) == tuple(cfg["points"])
                for k, v in s.items():
                    close(actual[k].to_numpy(), v)
                    close(np.array(summary.loc[m, k]), np.array(v.mean()))
                for p, point in enumerate(cfg["points"]):
                    row = daily[(daily.model == m) & (daily.point == point)]
                    assert list(row.date) == list(data["dates"][n:])
                    close(row.distance.to_numpy(), np.arange(1, end - n + 1))
                    close(row.observed.to_numpy(), y[n:end, p])
                    close(row["mean"].to_numpy(), means[m][:, p])
                    close(row.error.to_numpy(), means[m][:, p] - y[n:end, p])
                    close(row.sigma.to_numpy(), np.repeat(sigma[m][p], end - n))
                    for lev in (80, 90, 95):
                        q = ndtri((1 + lev / 100) / 2)
                        close(
                            row[f"lower{lev}"].to_numpy(),
                            means[m][:, p] - q * sigma[m][p],
                        )
                        close(
                            row[f"upper{lev}"].to_numpy(),
                            means[m][:, p] + q * sigma[m][p],
                        )
                if m != "DRIFT1":
                    fmu = (
                        data["mean"][:n]
                        if m == "BPLUS_CONTINUOUS"
                        else rr[:n]
                        if m == "RR_COND"
                        else old_tcn[m][:n]
                        if m in old_tcn
                        else ens[m][:n]
                    )
                    fs = independently_score(y[:n], fmu)
                    for k, v in fs.items():
                        close(fit[fit.model == m][k].to_numpy(), v)
            seed_avgs[phase] = {}
            for arm in cfg["arms"]:
                seed_avgs[phase][arm] = {}
                for seed in cfg["neural"]["seeds"]:
                    vals = independently_score(
                        y[n:end], generated[(arm, seed, steps[arm])][n:], sigma[arm]
                    )
                    seed_avgs[phase][arm][seed] = {
                        k: float(v.mean()) for k, v in vals.items()
                    }
                    rows = seeds[(seeds.model == arm) & (seeds.seed == seed)]
                    for k, v in vals.items():
                        close(rows[k].to_numpy(), v)
            stored_gates = read_json(directory / "effect_gates.json")
            base = values["BPLUS_CONTINUOUS"]
            for arm in cfg["arms"]:
                v = values[arm]
                mean_ok = all(
                    v[k].mean() <= base[k].mean() * 0.99
                    and np.all(v[k] <= base[k] + 1e-6)
                    for k in ("mae", "rmse")
                )
                prob_ok = all(
                    v[k].mean() <= base[k].mean() * 0.99
                    and np.all(v[k] <= base[k] * 1.05)
                    for k in ("crps", "interval_score90")
                )
                prob_ok = (
                    prob_ok
                    and v["coverage90"].mean() >= 0.85
                    and np.all(v["coverage90"] >= 0.8)
                )
                assert stored_gates[arm]["mean_pass"] == bool(mean_ok)
                assert stored_gates[arm]["probability_pass"] == bool(prob_ok)
                assert stored_gates[arm]["joint_pass"] == bool(mean_ok and prob_ok)
                for control in (
                    "BPLUS_CONTINUOUS",
                    "DRIFT1",
                    "RR_COND",
                    "TCN_DIRECT_COND",
                    "TCN_BRES_COND",
                    cfg["families"][
                        next(f for f, pair in cfg["families"].items() if arm in pair)
                    ][0],
                ):
                    for k in ("mae", "rmse", "crps", "interval_score90"):
                        comparison_rows.append(
                            dict(
                                phase=phase,
                                model=arm,
                                control=control,
                                metric=k,
                                model_value=float(v[k].mean()),
                                control_value=float(values[control][k].mean()),
                                difference=float(
                                    v[k].mean() - values[control][k].mean()
                                ),
                                points_improved=int(np.sum(v[k] < values[control][k])),
                            )
                        )
            if phase == "development":
                selection = read_json(root / "selection.json")
                for field, keys in (
                    ("mean_winner", ["mae", "rmse"]),
                    ("probability_winner", ["crps", "interval_score90"]),
                ):
                    remaining = list(cfg["methods"])
                    for k in keys:
                        minimum = min(values[m][k].mean() for m in remaining)
                        remaining = [
                            m
                            for m in remaining
                            if values[m][k].mean() <= minimum + 1e-12
                        ]
                    assert selection[field] == remaining[0]
                pool = {m: y[1078:1168] - means[m][-90:] for m in cfg["methods"]}
                nextcal = read_json(directory / "next_calibration.json")
                for m, v in pool.items():
                    close(
                        nextcal["scales"][m],
                        np.maximum(np.sqrt(np.mean(v * v, axis=0)), 1e-6),
                    )
                np.savez_compressed(dest / "development_calibration_errors.npz", **pool)
        events = [
            json.loads(x) for x in (root / "events.jsonl").read_text().splitlines()
        ]
        for phase, (n, end) in cfg["stages"].items():
            means_lock = next(
                i
                for i, e in enumerate(events)
                if e["event"] == "all_means_locked" and e["phase"] == phase
            )
            if phase == "internal":
                choose_read = next(
                    i
                    for i, e in enumerate(events)
                    if e.get("purpose") == "internal_update_selection"
                )
                choice_lock = next(
                    i for i, e in enumerate(events) if e["event"] == "updates_locked"
                )
                cal_read = next(
                    i
                    for i, e in enumerate(events)
                    if e.get("purpose") == "internal_initial_calibration"
                )
                assert means_lock < choose_read < choice_lock < cal_read
            else:
                dist_lock = next(
                    i
                    for i, e in enumerate(events)
                    if e["event"] == "distribution_locked" and e["phase"] == phase
                )
                label_read = next(
                    i
                    for i, e in enumerate(events)
                    if e.get("purpose") == phase + "_scoring_after_distribution_lock"
                )
                assert means_lock < dist_lock < label_read
        decision_event = next(
            i
            for i, e in enumerate(events)
            if e["event"] == "development_selection_locked"
        )
        final_start = next(
            i
            for i, e in enumerate(events)
            if e["event"] == "phase_started" and e["phase"] == "final_exploratory"
        )
        assert decision_event < final_start
        assert formal_fits == 36 and total_updates == 4800 + 12 * sum(update.values())
        assert sum(e["event"] == "fit_completed" for e in events) == 36
        assert sum(e["event"] == "ridge_fit_completed" for e in events) == 0
        assert sum(e["event"] == "controls_reused" for e in events) == 3
        pairing = {}
        for phase, v in seed_avgs.items():
            pairing[phase] = {}
            for family, pair in cfg["families"].items():
                counts = {}
                for label, keys in (
                    ("mean", ("mae", "rmse")),
                    ("probability", ("crps", "interval_score90")),
                ):
                    flags = [
                        all(v[pair[1]][s][k] < v[pair[0]][s][k] for k in keys)
                        for s in (0, 1, 2)
                    ]
                    counts[label] = dict(improving_seeds=sum(flags), by_seed=flags)
                pairing[phase][family] = counts
        pd.DataFrame(comparison_rows).to_csv(
            dest / "paired_comparisons.csv", index=False, float_format="%.17g"
        )
        write_json(dest / "residual_seed_agreement.json", pairing)
        state.update(
            status="passed",
            completed_utc=utc(),
            formal_fits_verified=formal_fits,
            optimizer_updates_verified=total_updates,
            training_loop_seconds=train_seconds,
            independent_forward="numpy explicit causal attention and serial Mamba-1 recurrence from all saved weights; exact PyTorch reload",
            ridge_verification="svd algebra, no new optimization",
            issue_order_verified=True,
            execution_errors=sum(e["event"] == "execution_error" for e in events),
            seed_agreement=pairing,
        )
        write_json(dest / "receipt.json", state)
        print(json.dumps(state, ensure_ascii=False), flush=True)
    except Exception:
        state.update(status="failed", error=traceback.format_exc())
        write_json(dest / "receipt.json", state)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", default="v1")
    main(parser.parse_args().attempt)
