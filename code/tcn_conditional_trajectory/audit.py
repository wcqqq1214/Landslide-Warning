"""Independent NumPy TCN forward, algebraic ridge, scores and issue-order audit."""

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


def convolution(x, w, bias, dilation):
    n = len(x)
    result = np.broadcast_to(bias, (n, len(bias))).copy()
    for k in range(w.shape[2]):
        offset = (w.shape[2] - 1 - k) * dilation
        if offset < n:
            result[offset:] += np.einsum(
                "ti,oi->to", x[: n - offset], w[:, :, k], optimize=False
            )
    return result


def numpy_forward(checkpoint, x, physical):
    state = {k: v.detach().numpy() for k, v in checkpoint["state_dict"].items()}
    scale = checkpoint["scaling"]
    z = (x - np.array(scale["x_mean"])) / np.array(scale["x_std"])
    for block, dilation in enumerate((1, 2, 4, 8)):
        p = f"encoder.{block}."
        first = np.maximum(
            convolution(
                z,
                state[p + "first.conv.weight"],
                state[p + "first.conv.bias"],
                dilation,
            ),
            0,
        )
        second = np.maximum(
            convolution(
                first,
                state[p + "second.conv.weight"],
                state[p + "second.conv.bias"],
                dilation,
            ),
            0,
        )
        skip = (
            convolution(z, state[p + "skip.weight"], state[p + "skip.bias"], 1)
            if p + "skip.weight" in state
            else z
        )
        z = np.maximum(second + skip, 0)
    output = convolution(z, state["head.weight"], state["head.bias"], 1)
    base = physical if checkpoint["arm"] == "TCN_BRES_COND" else np.array(scale["y0"])
    return base + np.array(scale["unit"]) * output


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
        assert update in cfg["neural"]["checkpoints"]
        stage_means, stage_sigmas, seed_avgs = {}, {}, {}
        formal_fits, total_updates, train_seconds = 0, 0, 0.0
        q_values = {str(k): [] for k in cfg["neural"]["checkpoints"]}
        comparison_rows = []
        for phase, (n, end) in cfg["stages"].items():
            directory = root / phase
            verify_manifest(directory / "mean_lock.json")
            verify_manifest(directory / "scoring_lock.json")
            data = load_npz(root / "implementation_verification" / f"teacher_{n}.npz")
            scaler = read_json(directory / "scaling.json")
            close(scaler["x_mean"], data["x"][:n].mean(0))
            close(scaler["x_std"], np.maximum(data["x"][:n].std(0), 1e-6))
            unit = np.maximum((y[:n] - y[0]).std(0), 1)
            close(scaler["unit"], unit)
            b = load_npz(directory / "baseline_predictions.npz")
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
            ridge = load_npz(directory / "ridge.npz")
            close(ridge["coef"], coef)
            zfull = (data["x"] - np.array(scaler["x_mean"])) / np.array(scaler["x_std"])
            rr = y[0] + unit * (zfull @ coef + intercept)
            close(b["RR_COND"], rr[n:])
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
                        close(original, mu, 1e-9)
                        f = read_json(d / f"e{saved['updates']}_fit.json")
                        close(
                            np.array(f["normalized_loss"]),
                            np.array(np.mean(((mu[:n] - y[:n]) / unit) ** 2)),
                        )
                        state["model_checkpoints"] += 1
                        generated[(arm, seed, saved["updates"])] = original
            # Paired e0 encoders are bytewise identical, and trained nonzero weights were used.
            for seed in cfg["neural"]["seeds"]:
                a = torch.load(
                    directory / cfg["arms"][0] / f"seed_{seed}/e0.pt", weights_only=True
                )
                b0 = torch.load(
                    directory / cfg["arms"][1] / f"seed_{seed}/e0.pt", weights_only=True
                )
                for k in a["state_dict"]:
                    torch.testing.assert_close(
                        a["state_dict"][k], b0["state_dict"][k], atol=0, rtol=0
                    )
            means = dict(b)
            ens = load_npz(directory / f"ensemble_e{update}.npz")
            for arm in cfg["arms"]:
                expected = np.mean(
                    [generated[(arm, s, update)] for s in cfg["neural"]["seeds"]],
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
                        q_values[str(step)].extend(
                            (
                                np.sqrt(
                                    np.mean(
                                        (expected[612:702] - y[612:702]) ** 2, axis=0
                                    )
                                )
                                / unit
                            ).tolist()
                        )
                quality = {k: float(np.mean(v)) for k, v in q_values.items()}
                for k, v in quality.items():
                    close(np.array(selected["quality"][k]), np.array(v))
                winner = min(
                    int(k)
                    for k, v in quality.items()
                    if v <= min(quality.values()) + 1e-12
                )
                assert winner == update
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
            assert len(daily) == 5 * 4 * (end - n)
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
                        y[n:end], generated[(arm, seed, update)][n:], sigma[arm]
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
        assert formal_fits == 18 and total_updates == 2400 + 12 * update
        assert sum(e["event"] == "fit_completed" for e in events) == 18
        assert sum(e["event"] == "ridge_fit_completed" for e in events) == 3
        pairing = {}
        for phase, v in seed_avgs.items():
            counts = {}
            for label, keys in (
                ("mean", ("mae", "rmse")),
                ("probability", ("crps", "interval_score90")),
            ):
                flags = [
                    all(
                        v["TCN_BRES_COND"][s][k] < v["TCN_DIRECT_COND"][s][k]
                        for k in keys
                    )
                    for s in (0, 1, 2)
                ]
                counts[label] = dict(improving_seeds=sum(flags), by_seed=flags)
            pairing[phase] = counts
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
            independent_forward="numpy causal convolutions from all saved weights",
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
