"""Independent saved-checkpoint/metric audit and preregistered diagnostics."""

import argparse
import json
import traceback

import numpy as np
import pandas as pd
import torch

from sequence_conditional.audit import independently_score
from sequence_conditional.independent import numpy_forward
from .core import (
    CONFIG,
    NEW,
    OLD,
    ROOT,
    guard_sources,
    load_npz,
    predict,
    read_json,
    read_labels,
    reload_model,
    sha,
    spec,
    utc,
    verify_lock,
    write_json,
)


def main(attempt):
    cfg = spec()
    root = ROOT / cfg["out"]
    dest = root / f"verification_{attempt}"
    dest.mkdir(exist_ok=False)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    receipt = dict(
        status="running",
        time_utc=utc(),
        formal_new_fits=0,
        optimizer_updates=0,
        model_checkpoints=0,
        numerical_values_checked=0,
        max_abs_difference=0.0,
    )

    def close(a, b, tol=1e-8):
        a, b = np.asarray(a, float), np.asarray(b, float)
        assert a.shape == b.shape, (a.shape, b.shape)
        delta = float(np.max(abs(a - b))) if a.size else 0.0
        receipt["max_abs_difference"] = max(receipt["max_abs_difference"], delta)
        receipt["numerical_values_checked"] += a.size
        np.testing.assert_allclose(a, b, atol=tol, rtol=0)

    def table_check(table, references, indices):
        for _, row in table.iterrows():
            key = tuple(row[k] for k in indices)
            reference = references[key]
            for name, value in reference.items():
                close(row[name], value)

    try:
        receipt["source_files_checked"] = guard_sources()
        for p, h in read_json(root / "implementation_lock.json")["files"].items():
            assert sha(ROOT / p) == h, p
        for phase in ("development", "final_exploratory"):
            verify_lock(root / phase / "distribution_lock.json")
        # All new means/distributions have been locked before this audit reads full labels.
        y = read_labels(ROOT / cfg["data"], 1461)
        all_means = {}
        all_sigmas = {}
        all_refs = {}
        fit_rows = []
        summary_rows = []
        point_rows = []
        seed_rows = []
        pair_rows = []
        correction_rows = []
        cross_rows = []
        cal_rows = []
        cal_summary = []
        total_fits = 0
        total_updates = 0
        training_seconds = 0.0
        stages = list(cfg["stages"])
        for phase, (n, end) in cfg["stages"].items():
            directory = root / phase
            olddir = ROOT / cfg["reuse_sequence"] / phase
            for name in ("mean_lock.json", "scoring_lock.json"):
                verify_lock(directory / name)
            data = load_npz(
                ROOT / cfg["reuse"] / f"implementation_verification/teacher_{n}.npz"
            )
            s = read_json(directory / "scaling.json")
            close(s["x_mean"], data["x"][:n].mean(0), 0)
            close(s["x_std"], np.maximum(data["x"][:n].std(0), 1e-6), 0)
            unit = np.maximum((y[:n] - y[0]).std(0), 1)
            close(s["unit"], unit, 0)
            close(s["y0"], y[0], 0)
            assert s["training_rows"] == n
            generated = []
            for seed in cfg["neural"]["seeds"]:
                d = directory / NEW / f"seed_{seed}"
                done = verify_lock(d / "complete.json")
                assert done["updates"] == 400 and done["rows"] == n
                total_fits += 1
                total_updates += done["updates"]
                training_seconds += done["elapsed_seconds"]
                logs = [
                    json.loads(line)
                    for line in (d / "training.jsonl").read_text().splitlines()
                ]
                assert [r["step"] for r in logs] == list(range(1, 401))
                for row in logs:
                    close(
                        row["pre_update_total"],
                        row["pre_update_loss"] + row["pre_update_penalty"],
                        1e-15,
                    )
                    assert (
                        all(np.isfinite(v) for v in row.values())
                        and row["grad_norm"] >= 0
                    )
                for e in (0, 50, 100, 200, 400):
                    model, scale, ck = reload_model(d / f"e{e}.pt", cfg)
                    assert ck["candidate"] == NEW and ck["regularization_lambda"] == 1.0
                    assert ck["training_prefix"] == n and ck["config_sha256"] == sha(
                        CONFIG
                    )
                    mu = np.load(d / f"e{e}_mean.npy")
                    assert mu.shape == (end, 4)
                    close(predict(model, scale, OLD, data["x"], data["mean"]), mu, 0)
                    close(numpy_forward(ck, data["x"], data["mean"]), mu, 1e-7)
                    if e == 0:
                        close(mu, data["mean"], 0)
                        old = torch.load(
                            olddir / OLD / f"seed_{seed}/e0.pt",
                            weights_only=True,
                            map_location="cpu",
                        )
                        for k, v in ck["state_dict"].items():
                            close(v, old["state_dict"][k], 0)
                    q = (mu[:n] - data["mean"][:n]) / unit
                    r = (y[:n] - data["mean"][:n]) / unit
                    meta = read_json(d / f"e{e}_fit.json")
                    dl = np.mean((q - r) ** 2)
                    pen = np.mean(q * q)
                    close(meta["normalized_data_loss"], dl, 1e-12)
                    close(meta["normalized_penalty"], pen, 1e-12)
                    close(meta["normalized_total"], dl + pen, 1e-12)
                    close(
                        dl + pen,
                        2 * np.mean((q - r / 2) ** 2) + np.mean(r * r) / 2,
                        1e-12,
                    )
                    close(
                        meta["correction_rms_mm"],
                        np.sqrt(np.mean((mu[:n] - data["mean"][:n]) ** 2, 0)),
                    )
                    fit = independently_score(y[:n], mu[:n])
                    for p in range(4):
                        close(
                            [meta["metrics"][p][k] for k in ("mae", "rmse")],
                            [fit[k][p] for k in ("mae", "rmse")],
                        )
                    receipt["model_checkpoints"] += 1
                generated.append(mu)
            ensemble = np.mean(generated, axis=0)
            close(load_npz(directory / "ensemble_selected.npz")[NEW], ensemble, 0)
            means = load_npz(directory / "reused_predictions.npz")
            means[NEW] = ensemble[n:]
            oldens = load_npz(olddir / "ensemble_selected.npz")[OLD]
            oldreuse = load_npz(olddir / "reused_predictions.npz")
            for m in cfg["reuse_methods"]:
                close(means[m], oldens[n:] if m == OLD else oldreuse[m], 0)
            close(means["BPLUS_CONTINUOUS"], data["mean"][n:], 0)
            close(
                means["DRIFT1"],
                y[n - 1] + np.arange(1, end - n + 1)[:, None] * (y[n - 1] - y[n - 2]),
                0,
            )
            all_means[phase] = means
            if phase != "final_exploratory":
                cal = read_json(directory / "next_calibration.json")
                lo, hi = [702, 792] if phase == "internal" else [1078, 1168]
                assert (
                    cal["error_indices"] == [lo, hi]
                    and cal["last_label_index"] == hi - 1
                    and cal["next_origin"] == hi
                )
                for m in cfg["methods"]:
                    errors = y[lo:hi] - means[m][lo - n : hi - n]
                    close(
                        cal["scales"][m],
                        np.maximum(np.sqrt(np.mean(errors**2, 0)), 1e-6),
                        0,
                    )
            if phase == "internal":
                for fname, limit in [
                    ("first90_diagnostic.csv", 90),
                    ("metrics_by_point.csv", 180),
                ]:
                    refs = {}
                    for m in cfg["methods"]:
                        val = independently_score(y[n : n + limit], means[m][:limit])
                        for p, point in enumerate(cfg["points"]):
                            refs[(m, point)] = {k: v[p] for k, v in val.items()}
                    table_check(
                        pd.read_csv(directory / fname), refs, ["model", "point"]
                    )
                continue
            prev = stages[stages.index(phase) - 1]
            cal = read_json(root / prev / "next_calibration.json")
            issue = load_npz(directory / "issued_distribution.npz")
            np.testing.assert_array_equal(issue["dates"], data["dates"][n:])
            sigmas = {m: issue[m + "__sigma"] for m in cfg["methods"]}
            all_sigmas[phase] = sigmas
            refs = {}
            summary = {}
            for m in cfg["methods"]:
                close(issue[m + "__mean"], means[m], 0)
                close(sigmas[m], cal["scales"][m], 0)
                score = (
                    independently_score(y[n:], means[m], sigmas[m])
                    if end == 1461
                    else independently_score(y[n:end], means[m], sigmas[m])
                )
                summary[m] = {k: float(v.mean()) for k, v in score.items()}
                summary_rows.append(
                    dict(phase=phase, model=m, n_per_point=end - n, **summary[m])
                )
                for p, point in enumerate(cfg["points"]):
                    refs[(m, point)] = {k: v[p] for k, v in score.items()}
                    point_rows.append(
                        dict(
                            phase=phase,
                            model=m,
                            point=point,
                            n=end - n,
                            **refs[(m, point)],
                        )
                    )
            table_check(
                pd.read_csv(directory / "metrics_by_point.csv"),
                refs,
                ["model", "point"],
            )
            table_check(
                pd.read_csv(directory / "summary.csv"),
                {(m,): v for m, v in summary.items()},
                ["model"],
            )
            all_refs[phase] = summary
            for m, mu in [
                ("BPLUS_CONTINUOUS", data["mean"]),
                (OLD, oldens),
                (NEW, ensemble),
            ]:
                fit = independently_score(y[:n], mu[:n])
                fit_rows.append(
                    dict(
                        phase=phase,
                        model=m,
                        **{k: float(v.mean()) for k, v in fit.items()},
                    )
                )
                for part, a, b in [("training", 0, n), ("forecast", n, end)]:
                    rms = np.sqrt(np.mean((mu[a:b] - data["mean"][a:b]) ** 2, 0))
                    for p, point in enumerate(cfg["points"]):
                        correction_rows.append(
                            dict(
                                phase=phase,
                                model=m,
                                part=part,
                                point=point,
                                correction_rms_mm=rms[p],
                            )
                        )
            fitting = pd.read_csv(directory / "fitting_by_point.csv")
            for _, row in fitting.iterrows():
                mu = {"BPLUS_CONTINUOUS": data["mean"], OLD: oldens, NEW: ensemble}[
                    row["model"]
                ]
                val = independently_score(y[:n], mu[:n])
                p = cfg["points"].index(row["point"])
                close(
                    [row[k] for k in ("mae", "rmse")],
                    [val[k][p] for k in ("mae", "rmse")],
                )
            seeds = {}
            seedrefs = {}
            for m in (OLD, NEW):
                for seed in cfg["neural"]["seeds"]:
                    src = olddir if m == OLD else directory
                    mu = np.load(src / m / f"seed_{seed}/e400_mean.npy")[n:]
                    vals = independently_score(y[n:end], mu, sigmas[m])
                    avg = {k: float(v.mean()) for k, v in vals.items()}
                    seeds[(m, seed)] = avg
                    seed_rows.append(dict(phase=phase, model=m, seed=seed, **avg))
                    for p, point in enumerate(cfg["points"]):
                        seedrefs[(m, seed, point)] = {k: v[p] for k, v in vals.items()}
            table_check(
                pd.read_csv(directory / "seed_metrics.csv"),
                seedrefs,
                ["model", "seed", "point"],
            )
            for typ, keys in [
                ("mean", ("mae", "rmse")),
                ("probability", ("crps", "interval_score90")),
            ]:
                count = sum(
                    all(seeds[(NEW, s)][k] < seeds[(OLD, s)][k] for k in keys)
                    for s in cfg["neural"]["seeds"]
                )
                agree = all(summary[NEW][k] < summary[OLD][k] for k in keys)
                pair_rows.append(
                    dict(
                        phase=phase,
                        type=typ,
                        ensemble_improved=agree,
                        seed_agreement=count,
                        paired_pass=agree and count >= 2,
                    )
                )
            # Recompute gate booleans directly from independent point and aggregate metrics.
            gatefile = read_json(directory / "effect_gates.json")
            ef = cfg["effect"]
            for m in (OLD, NEW):
                mean = all(
                    summary[m][k] <= summary["BPLUS_CONTINUOUS"][k] * 0.99
                    for k in ("mae", "rmse")
                )
                mean &= all(
                    refs[(m, p)][k] <= refs[("BPLUS_CONTINUOUS", p)][k] + 1e-6
                    for p in cfg["points"]
                    for k in ("mae", "rmse")
                )
                prob = all(
                    summary[m][k] <= summary["BPLUS_CONTINUOUS"][k] * 0.99
                    for k in ("crps", "interval_score90")
                )
                prob &= all(
                    refs[(m, p)][k] <= refs[("BPLUS_CONTINUOUS", p)][k] * 1.05
                    for p in cfg["points"]
                    for k in ("crps", "interval_score90")
                )
                prob &= summary[m]["coverage90"] >= ef[
                    "coverage90_average_min"
                ] and all(refs[(m, p)]["coverage90"] >= 0.8 for p in cfg["points"])
                assert (
                    gatefile[m]["mean_pass"] == mean
                    and gatefile[m]["probability_pass"] == prob
                    and gatefile[m]["joint_pass"] == (mean and prob)
                )
            daily = pd.read_csv(directory / "daily_predictions.csv")
            assert len(daily) == len(cfg["methods"]) * (end - n) * 4
            from scipy.special import ndtri

            for m in cfg["methods"]:
                for p, point in enumerate(cfg["points"]):
                    f = daily[(daily.model == m) & (daily.point == point)]
                    np.testing.assert_array_equal(f.date.to_numpy(), data["dates"][n:])
                    close(f.distance, np.arange(1, end - n + 1), 0)
                    close(f.observed, y[n:end, p])
                    close(f["mean"], means[m][:, p])
                    close(f.error, means[m][:, p] - y[n:end, p])
                    close(f.sigma, np.full(end - n, sigmas[m][p]))
                    for level in (80, 90, 95):
                        z = ndtri((1 + level / 100) / 2)
                        close(f[f"lower{level}"], means[m][:, p] - z * sigmas[m][p])
                        close(f[f"upper{level}"], means[m][:, p] + z * sigmas[m][p])
            for m in cfg["cross_diagnostics"]:
                lo, hi = cal["error_indices"]
                pn = cfg["stages"][prev][0]
                errors = y[lo:hi] - all_means[prev][m][lo - pn : hi - pn]
                for p, point in enumerate(cfg["points"]):
                    for i in range(90):
                        cal_rows.append(
                            dict(
                                phase=phase,
                                model=m,
                                point=point,
                                date=str(data["dates"][lo + i]),
                                error_mm=errors[i, p],
                            )
                        )
                    bias = float(errors[:, p].mean())
                    std = float(errors[:, p].std())
                    rms = float(np.sqrt(np.mean(errors[:, p] ** 2)))
                    close(rms * rms, bias * bias + std * std, 1e-10)
                    cal_summary.append(
                        dict(
                            phase=phase,
                            model=m,
                            point=point,
                            bias_mm=bias,
                            std_mm=std,
                            rms_mm=rms,
                            issued_sigma_mm=sigmas[m][p],
                            forecast_rmse_mm=refs[(m, point)]["rmse"],
                        )
                    )
                for sigma_source in cfg["cross_diagnostics"]:
                    sc = independently_score(y[n:end], means[m], sigmas[sigma_source])
                    for p, point in enumerate(cfg["points"]):
                        cross_rows.append(
                            dict(
                                phase=phase,
                                mean_source=m,
                                sigma_source=sigma_source,
                                point=point,
                                diagnostic_only=True,
                                **{k: v[p] for k, v in sc.items()},
                            )
                        )
        assert (total_fits, total_updates, receipt["model_checkpoints"]) == (
            9,
            3600,
            45,
        )
        # Event ledger independently confirms stage and label-release order.
        events = [
            json.loads(line)
            for line in (root / "events.jsonl").read_text().splitlines()
        ]
        receipt["execution_errors"] = sum(
            e["event"] == "execution_error" for e in events
        )
        assert receipt["execution_errors"] == 0
        positions = {}
        for phase, (n, end) in cfg["stages"].items():

            def loc(kind):
                return next(
                    i
                    for i, e in enumerate(events)
                    if e["event"] == kind and e.get("phase") == phase
                )

            start = loc("phase_started")
            done = loc("phase_completed")
            mean = loc("all_means_locked")
            positions[phase] = (start, done)
            assert start < mean < done
            for i, e in enumerate(events):
                if start <= i <= done and e["event"] == "label_prefix_read":
                    if i < mean:
                        assert e["rows"] <= n
                    if phase != "internal" and e["rows"] > n:
                        assert i > loc("distribution_locked")
            if phase != "internal":
                assert mean < loc("distribution_locked") < done
        assert (
            positions["internal"][1]
            < positions["development"][0]
            < positions["development"][1]
            < positions["final_exploratory"][0]
        )
        selection = read_json(root / "selection.json")
        for field, keys in [
            ("mean_winner", ("mae", "rmse")),
            ("probability_winner", ("crps", "interval_score90")),
        ]:
            ordered = sorted(
                cfg["methods"],
                key=lambda m: tuple(all_refs["development"][m][k] for k in keys),
            )
            assert selection[field] == ordered[0]
        locked = next(
            i
            for i, e in enumerate(events)
            if e["event"] == "development_selection_locked"
        )
        assert locked < positions["final_exploratory"][0]
        assert (
            selection
            == read_json(root / "final_exploratory/frozen_selection_evaluation.json")[
                "selection"
            ]
        )
        for name, rows in [
            ("phase_summary", summary_rows),
            ("metrics_by_point", point_rows),
            ("fitting_summary", fit_rows),
            ("seed_summary", seed_rows),
            ("regularization_pairing", pair_rows),
            ("correction_amplitudes", correction_rows),
            ("cross_scoring", cross_rows),
            ("calibration_errors", cal_rows),
            ("calibration_transfer", cal_summary),
        ]:
            pd.DataFrame(rows).to_csv(
                dest / f"{name}.csv", index=False, float_format="%.17g"
            )
        receipt.update(
            status="passed",
            formal_fits_audited=total_fits,
            formal_updates_audited=total_updates,
            formal_training_seconds=training_seconds,
            source_control_refits=0,
            cross_combinations=18,
            cross_point_rows=len(cross_rows),
            calibration_error_rows=len(cal_rows),
            event_rows=len(events),
            verification_end_utc=utc(),
        )
    except Exception:
        receipt.update(status="failed", traceback=traceback.format_exc())
        raise
    finally:
        write_json(dest / "receipt.json", receipt)
    print(receipt)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", default="v1")
    main(parser.parse_args().attempt)
