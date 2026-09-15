"""Replay immutable KIN checkpoints; no fitting, selection or recalibration."""

import json
import traceback
import numpy as np
import pandas as pd
from . import core as c


def error_rows(cfg, truth, prediction, sigma, metadata):
    rows = []
    for b in cfg["bins"]:
        sl = slice(b["start"] - 1, b["end"])
        for p, r in enumerate(c.o.scores(truth[sl], prediction[sl], sigma)):
            e = prediction[sl, p] - truth[sl, p]
            rows.append(
                dict(
                    **metadata,
                    bin=b["name"],
                    **r,
                    bias=float(e.mean()),
                    mse=float((e * e).mean()),
                    error_variance=float(e.var()),
                    probability_available=sigma is not None,
                )
            )
    return rows


def summary(frame, groups):
    numeric = [
        v
        for v in frame.select_dtypes(include=np.number).columns
        if v not in groups and v not in ["n", "n_terms", "supported_origins"]
    ]
    return frame.groupby(groups, sort=False, dropna=False)[numeric].mean().reset_index()


def main(attempt):
    cfg, o = c.spec(), c.o
    o.setup(cfg)
    sources = c.guard()
    root = c.ROOT / cfg["out"]
    out = root / attempt
    out.mkdir(exist_ok=False)
    receipt = dict(
        status="running",
        new_fits=0,
        optimizer_updates=0,
        new_bplus_fits=0,
        new_physical_forwards=0,
    )
    try:
        y = o.read_labels(c.ROOT / cfg["data"], 1461)
        forcing, _ = o.read_forcing(c.ROOT / cfg["data"], 1461)
        teachers = o.bank(c.prior.spec())
        weights, training, forecasts, scores, decompositions, tail, sensitivities = (
            [] for _ in range(7)
        )
        replay, visibility = {}, {}
        replayed = 0
        for n in cfg["origins"]:
            o.check_deadline(cfg)
            truth = y[n : n + 293]
            source = c.ROOT / cfg["prior_out"] / f"origin_{n}"
            o.verify_lock(source / "issue_lock.json")
            means, seeds = [
                o.load_npz(source / (key + ".npz")) for key in ["means", "seeds"]
            ]
            sigmas = o.load_npz(source / "sigmas.npz") if n != 612 else {}
            step_paths = {step: [] for step in cfg["steps"]}
            for seed in cfg["seeds"]:
                schedule = np.load(c.folder(cfg, n, seed) / "schedule.npz")["origins"]
                for step in cfg["steps"]:
                    v = c.supervision(n, schedule, step)
                    for h in range(293):
                        weights.append(
                            dict(
                                origin=n,
                                seed=seed,
                                step=step,
                                horizon=h + 1,
                                **{k: value[h] for k, value in v.items()},
                            )
                        )
                _, sc0, _ = c.reload(cfg, n, seed, 0)
                data = c.inputs(teachers, y[:n], forcing, n, sc0, np.arange(432, n))
                full = c.inputs(teachers, y[:n], forcing, n, sc0)
                unit = np.asarray(sc0["unit"])
                for step in cfg["steps"]:
                    o.check_deadline(cfg)
                    model, sc, _ = c.reload(cfg, n, seed, step)
                    assert sc == sc0
                    q = c.model_forward(model, data, cfg["replay_batch"])
                    rows = c.training_stats(
                        q, data[2].numpy(), data[3].numpy(), unit, cfg["bins"]
                    )
                    for r in rows:
                        r["point"] = cfg["points"][r["point"]]
                        training.append(dict(origin=n, seed=str(seed), step=step, **r))
                    qi = c.model_forward(model, full)[0]
                    mu = y[n - 1] + qi[1:] * unit
                    original = np.load(c.folder(cfg, n, seed) / f"e{step}_mean.npy")
                    assert np.array_equal(mu, original)
                    step_paths[step].append(mu)
                    replay[f"n{n}_s{seed}_e{step}"] = mu
                    forecasts += error_rows(
                        cfg, truth, mu, None, dict(origin=n, seed=str(seed), step=step)
                    )
                    assert all(
                        p.grad is None and not p.requires_grad
                        for p in model.parameters()
                    )
                    replayed += 1
                    if step == 400:
                        for d in cfg["visibility"]:
                            v = c.inputs(teachers, y[:n], forcing, n, sc, visible=d)
                            qm = c.model_forward(model, v)[0]
                            pm = y[n - 1] + qm[1:] * unit
                            visibility[f"n{n}_s{seed}_d{d}"] = pm
                            if d == 293:
                                assert np.array_equal(pm, mu)
                del data
            for step, paths in step_paths.items():
                forecasts += error_rows(
                    cfg,
                    truth,
                    np.mean(paths, axis=0),
                    None,
                    dict(origin=n, seed="ensemble", step=step),
                )
            for method in cfg["methods"]:
                sigma = sigmas.get(method)
                for seed, mu in [("ensemble", means[method])] + [
                    (str(s), seeds[method][s]) for s in cfg["seeds"]
                ]:
                    scores += error_rows(
                        cfg, truth, mu, sigma, dict(origin=n, method=method, seed=seed)
                    )
                    e = mu - truth
                    for p, point in enumerate(cfg["points"]):
                        total = float(np.sum(e[:, p] ** 2))
                        tail.append(
                            dict(
                                origin=n,
                                method=method,
                                seed=seed,
                                point=point,
                                total_sse=total,
                                tail_sse=float(np.sum(e[180:, p] ** 2)),
                                tail_fraction=float(np.sum(e[180:, p] ** 2) / total)
                                if total
                                else np.nan,
                            )
                        )
                for b in cfg["bins"]:
                    sl = slice(b["start"] - 1, b["end"])
                    ensemble = means[method][sl]
                    individual = seeds[method][:, sl]
                    ensemble_error = ensemble - truth[sl]
                    seed_error = individual - truth[sl]
                    for p, point in enumerate(cfg["points"]):
                        err = ensemble_error[:, p]
                        smse = float(np.mean(seed_error[:, :, p] ** 2))
                        mse = float(np.mean(err**2))
                        spread = float(
                            np.mean((individual[:, :, p] - ensemble[:, p]) ** 2)
                        )
                        decompositions.append(
                            dict(
                                origin=n,
                                method=method,
                                bin=b["name"],
                                point=point,
                                ensemble_mse=mse,
                                bias_squared=float(err.mean() ** 2),
                                error_variance=float(err.var()),
                                mean_seed_mse=smse,
                                forecast_spread_mse=spread,
                                ensemble_reduction_fraction=spread / smse
                                if smse
                                else np.nan,
                            )
                        )
            for d in cfg["visibility"]:
                masked = np.stack([visibility[f"n{n}_s{s}_d{d}"] for s in cfg["seeds"]])
                reference = seeds["TiDE_KIN"]
                for seed, a, b in [("ensemble", masked.mean(0), reference.mean(0))] + [
                    (str(s), masked[s], reference[s]) for s in cfg["seeds"]
                ]:
                    delta = (a - b)[:d]
                    for p, point in enumerate(cfg["points"]):
                        v = delta[:, p]
                        sensitivities.append(
                            dict(
                                origin=n,
                                visible=d,
                                seed=seed,
                                point=point,
                                common_horizons=d,
                                rms_change=float(np.sqrt(np.mean(v * v))),
                                mean_change=float(v.mean()),
                                mae_change=float(abs(v).mean()),
                                max_abs_change=float(abs(v).max()),
                                performance_comparison=False,
                            )
                        )
            o.event(
                root,
                "prefix_replayed",
                origin=n,
                checkpoints=replayed,
                new_training=0,
                purpose="diagnostic_not_new_forecast",
            )
            print(f"Completed origin {n}: {replayed}/60 fixed checkpoints", flush=True)
        frames = {
            "supervision_weights": pd.DataFrame(weights),
            "training_by_point": pd.DataFrame(training),
            "checkpoint_forecast_by_point": pd.DataFrame(forecasts),
            "issued_by_point": pd.DataFrame(scores),
            "mse_decomposition": pd.DataFrame(decompositions),
            "tail_sse": pd.DataFrame(tail),
            "visibility_sensitivity": pd.DataFrame(sensitivities),
        }
        frames["training_summary"] = summary(
            frames["training_by_point"], ["origin", "seed", "step", "bin"]
        )
        # Each point contribution already includes the common1/4 factor.
        frames["training_summary"]["objective_contribution"] *= 4
        frames["checkpoint_forecast_summary"] = summary(
            frames["checkpoint_forecast_by_point"], ["origin", "seed", "step", "bin"]
        )
        frames["issued_summary"] = summary(
            frames["issued_by_point"], ["origin", "method", "seed", "bin"]
        )
        for name, frame in frames.items():
            frame.to_csv(out / (name + ".csv"), index=False)
        np.savez_compressed(out / "checkpoint_forecasts.npz", **replay)
        np.savez_compressed(out / "visibility_forecasts.npz", **visibility)
        c.guard()
        receipt.update(
            status="complete",
            source_files=sources,
            checkpoints=replayed,
            training_origin_evaluations=sum(n - 432 for n in cfg["origins"]) * 15,
            visibility_seed_paths=len(visibility),
            rows={k: len(v) for k, v in frames.items()},
            all_fixed_diagnostics_complete=True,
            checkpoint_selection=False,
            original_forecasts_changed=False,
            bootstrap_probability="unavailable",
        )
    except Exception:
        receipt.update(status="failed", error=traceback.format_exc())
        raise
    finally:
        o.write_json(out / "receipt.json", receipt)
    o.lock(out, "lock.json", list(out.glob("*")), status="complete")
    o.write_json(
        root / "diagnostic_lock.json",
        dict(
            time_utc=o.utc(),
            status="complete",
            files={
                str((out / "lock.json").relative_to(root)): o.sha(out / "lock.json")
            },
            attempt=attempt,
        ),
    )
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", default="diagnostic_v1")
    main(parser.parse_args().attempt)
