"""Fixed training, pre-label prediction locks, and unconditional full evaluation."""

import argparse
import json
import platform
import time
import traceback

import numpy as np
import pandas as pd
import torch
from scipy.special import ndtri

from .core import (
    ARMS,
    ROOT,
    Scaling,
    TrajectoryTCN,
    array_sha,
    baseline,
    calibrate,
    check_deadline,
    choose,
    drift,
    fit_ridge,
    gates,
    guard_sources,
    load_npz,
    predict,
    read_json,
    read_labels,
    ridge_predict,
    scores,
    sha,
    spec,
    summarize,
    utc,
    write_json,
)


def event(root, kind, **values):
    record = dict(time_utc=utc(), event=kind, **values)
    with (root / "events.jsonl").open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")


def labels(cfg, root, end, purpose):
    event(root, "label_prefix_read", rows=end, last_index=end - 1, purpose=purpose)
    return read_labels(ROOT / cfg["data"], end)


def verify_lock(path):
    item = read_json(path)
    for name, digest in item["files"].items():
        if sha(path.parent / name) != digest:
            raise ValueError("Issued artifact changed: " + name)
    return item


def lock(directory, name, paths, **extra):
    value = dict(
        time_utc=utc(),
        **extra,
        files={str(p.relative_to(directory)): sha(p) for p in sorted(paths)},
    )
    write_json(directory / name, value)
    return value


def train_one(cfg, root, phase, arm, seed, updates, data, y, scale):
    dest = root / phase / arm / f"seed_{seed}"
    done = dest / "complete.json"
    if done.exists():
        verify_lock(done)
        return read_json(done)
    dest.mkdir(parents=True, exist_ok=False)
    n = len(y)
    model = TrajectoryTCN(cfg, seed)
    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=cfg["neural"]["lr"],
        betas=tuple(cfg["neural"]["betas"]),
        eps=cfg["neural"]["eps"],
        weight_decay=0,
    )
    train_x = scale.tensor(data["x"][:n])
    target = torch.tensor(
        (y - baseline(arm, data["mean"][:n], scale)) / scale.unit, dtype=torch.float64
    )
    checkpoints = [0] + [e for e in cfg["neural"]["checkpoints"] if e <= updates]
    started = time.monotonic()
    event(root, "fit_started", phase=phase, arm=arm, seed=seed, updates=updates, rows=n)
    with (dest / "training.jsonl").open("w") as log:
        for step in range(updates + 1):
            check_deadline(cfg)
            if step:
                model.train()
                optimizer.zero_grad(set_to_none=True)
                loss = (model(train_x) - target).square().mean()
                if not torch.isfinite(loss):
                    raise ArithmeticError("Nonfinite full-prefix objective")
                loss.backward()
                grad = float(
                    sum(
                        p.grad.square().sum()
                        for p in model.parameters()
                        if p.grad is not None
                    ).sqrt()
                )
                if not np.isfinite(grad):
                    raise ArithmeticError("Nonfinite gradient")
                optimizer.step()
                log.write(
                    json.dumps(
                        dict(
                            step=step,
                            pre_update_loss=float(loss.detach()),
                            gradient_norm=grad,
                            time_utc=utc(),
                        )
                    )
                    + "\n"
                )
                log.flush()
            if step in checkpoints:
                mu = predict(model, scale, arm, data["x"], data["mean"])
                fit_loss = float(np.mean(((mu[:n] - y) / scale.unit) ** 2))
                torch.save(
                    dict(
                        state_dict=model.state_dict(),
                        optimizer_state=optimizer.state_dict(),
                        scaling=scale.state,
                        seed=seed,
                        arm=arm,
                        updates=step,
                        training_prefix=n,
                        config_sha256=sha(
                            ROOT / "config/ootang_tcn_conditional_trajectory.v1_0.json"
                        ),
                    ),
                    dest / f"e{step}.pt",
                )
                np.save(dest / f"e{step}_mean.npy", mu)
                write_json(
                    dest / f"e{step}_fit.json",
                    dict(
                        step=step,
                        normalized_loss=fit_loss,
                        metrics=scores(y, mu[:n]),
                        mean_sha256=array_sha(mu),
                    ),
                )
                print(
                    f"{phase} {arm} seed={seed} update={step} loss={fit_loss:.8f}",
                    flush=True,
                )
    record = lock(
        dest,
        "complete.json",
        list(dest.glob("*")),
        status="fit_complete",
        phase=phase,
        arm=arm,
        seed=seed,
        updates=updates,
        rows=n,
        elapsed_seconds=time.monotonic() - started,
    )
    event(
        root,
        "fit_completed",
        phase=phase,
        arm=arm,
        seed=seed,
        updates=updates,
        elapsed_seconds=record["elapsed_seconds"],
    )
    return record


def write_scores(directory, phase, y, means, sigmas, fit_y, fit_means, dates, cfg):
    metrics, summary, fitting = {}, {}, []
    for name in cfg["methods"]:
        metrics[name] = scores(y, means[name], sigmas[name])
        summary[name] = summarize(metrics[name])
        if name in fit_means:
            fitting.extend(
                dict(model=name, **row) for row in scores(fit_y, fit_means[name])
            )
    point_rows = [
        dict(model=name, **row) for name, rows in metrics.items() for row in rows
    ]
    pd.DataFrame(point_rows).to_csv(
        directory / "metrics_by_point.csv", index=False, float_format="%.17g"
    )
    pd.DataFrame([dict(model=m, **v) for m, v in summary.items()]).to_csv(
        directory / "summary.csv", index=False, float_format="%.17g"
    )
    pd.DataFrame(fitting).to_csv(
        directory / "fitting_by_point.csv", index=False, float_format="%.17g"
    )
    rows = []
    for name in cfg["methods"]:
        for i, date in enumerate(dates):
            for p, point in enumerate(cfg["points"]):
                r = dict(
                    model=name,
                    date=str(date),
                    distance=i + 1,
                    point=point,
                    observed=float(y[i, p]),
                    mean=float(means[name][i, p]),
                    error=float(means[name][i, p] - y[i, p]),
                    sigma=float(sigmas[name][p]),
                )
                for lev in cfg["calibration"]["levels"]:
                    z = ndtri((1 + lev) / 2)
                    r[f"lower{round(100 * lev)}"] = float(
                        means[name][i, p] - z * sigmas[name][p]
                    )
                    r[f"upper{round(100 * lev)}"] = float(
                        means[name][i, p] + z * sigmas[name][p]
                    )
                rows.append(r)
    pd.DataFrame(rows).to_csv(
        directory / "daily_predictions.csv", index=False, float_format="%.17g"
    )
    write_json(directory / "effect_gates.json", gates(metrics, cfg))
    return metrics, summary


def run(phase):
    cfg = spec()
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    guard_sources()
    check_deadline(cfg)
    root = ROOT / cfg["out"]
    implementation = read_json(root / "implementation_lock.json")
    for name, digest in implementation["files"].items():
        if sha(ROOT / name) != digest:
            raise ValueError("Implementation differs from pre-training lock: " + name)
    prep = root / "implementation_verification"
    receipt = read_json(prep / "receipt.json")
    assert receipt["status"] == "passed"
    for name, digest in receipt["files"].items():
        assert sha(prep / name) == digest
    n, end = cfg["stages"][phase]
    directory = root / phase
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "scoring_lock.json").exists():
        verify_lock(directory / "scoring_lock.json")
        print(phase + " already completed; no new fitting", flush=True)
        return
    if phase != "internal":
        selected = read_json(root / "internal_selection.json")["selected_updates"]
        previous = "internal" if phase == "development" else "development"
        verify_lock(root / previous / "scoring_lock.json")
        cal = read_json(root / previous / "next_calibration.json")
        if cal["next_origin"] != n or cal["last_label_index"] >= n:
            raise ValueError("Calibration is not mature at this origin")
        if phase == "final_exploratory":
            selection = read_json(root / "selection.json")
            assert selection["source_phase"] == "development"
    else:
        selected = max(cfg["neural"]["checkpoints"])
    event(
        root,
        "phase_started",
        phase=phase,
        training_rows=n,
        prediction_rows=end - n,
        given_forcing_last_index=end - 1,
        selected_updates=selected,
    )
    data = load_npz(prep / f"teacher_{n}.npz")
    y = labels(cfg, root, n, phase + "_training")
    scale = Scaling(data["x"][:n], y, cfg=cfg)
    write_json(directory / "scaling.json", scale.state)
    ridge_file = directory / "ridge.npz"
    if ridge_file.exists():
        ridge = load_npz(ridge_file)
    else:
        ridge = fit_ridge(data["x"][:n], y, scale, cfg["ridge"]["alpha"])
        np.savez_compressed(ridge_file, **ridge)
        event(root, "ridge_fit_completed", phase=phase, rows=n)
    rr = ridge_predict(ridge, data["x"])
    basic = dict(
        BPLUS_CONTINUOUS=data["mean"][n:], DRIFT1=drift(y, end - n), RR_COND=rr[n:]
    )
    np.savez_compressed(directory / "baseline_predictions.npz", **basic)
    fits = []
    for arm in ARMS:
        for seed in cfg["neural"]["seeds"]:
            fits.append(
                train_one(cfg, root, phase, arm, seed, selected, data, y, scale)
            )
    checkpoints = cfg["neural"]["checkpoints"] if phase == "internal" else [selected]
    for step in checkpoints:
        ensemble = {
            arm: np.mean(
                [
                    np.load(directory / arm / f"seed_{seed}" / f"e{step}_mean.npy")
                    for seed in cfg["neural"]["seeds"]
                ],
                axis=0,
            )
            for arm in ARMS
        }
        np.savez_compressed(directory / f"ensemble_e{step}.npz", **ensemble)
    paths = list(directory.glob("**/*.pt")) + list(directory.glob("**/*.npy"))
    paths += list(directory.glob("*.npz")) + [directory / "scaling.json"]
    if not (directory / "mean_lock.json").exists():
        lock(
            directory,
            "mean_lock.json",
            paths,
            phase=phase,
            forecast_start=n,
            forecast_end=end,
            latest_training_label_index=n - 1,
            displacement_feedback=False,
        )
        event(
            root,
            "all_means_locked",
            phase=phase,
            lock_sha256=sha(directory / "mean_lock.json"),
        )
    else:
        verify_lock(directory / "mean_lock.json")
    if phase == "internal":
        ys = labels(cfg, root, 702, "internal_update_selection")
        qualities = {}
        for step in checkpoints:
            ensemble = load_npz(directory / f"ensemble_e{step}.npz")
            qualities[str(step)] = float(
                np.mean(
                    [
                        np.sqrt(
                            np.mean((ensemble[a][612:702] - ys[612:702]) ** 2, axis=0)
                        )
                        / scale.unit
                        for a in ARMS
                    ]
                )
            )
        best = min(qualities.values())
        selected = min(
            int(k)
            for k, v in qualities.items()
            if v <= best + cfg["selection"]["tie_tolerance"]
        )
        write_json(
            root / "internal_selection.json",
            dict(
                time_utc=utc(),
                quality=qualities,
                selected_updates=selected,
                selection_indices=[612, 702],
                source_mean_lock_sha256=sha(directory / "mean_lock.json"),
            ),
        )
        event(root, "updates_locked", selected_updates=selected, quality=qualities)
        yc = labels(cfg, root, 792, "internal_initial_calibration")
        ensemble = load_npz(directory / f"ensemble_e{selected}.npz")
        means = {**basic, **{a: ensemble[a][n:] for a in ARMS}}
        next_scales = {
            m: calibrate(yc[702:792] - means[m][90:180], cfg).tolist()
            for m in cfg["methods"]
        }
        write_json(
            directory / "next_calibration.json",
            dict(
                time_utc=utc(),
                next_origin=792,
                error_indices=[702, 792],
                last_label_index=791,
                scales=next_scales,
                source_mean_lock_sha256=sha(directory / "mean_lock.json"),
            ),
        )
        pd.DataFrame(
            [
                dict(model=m, **r)
                for m in cfg["methods"]
                for r in scores(yc[n:], means[m])
            ]
        ).to_csv(directory / "metrics_by_point.csv", index=False, float_format="%.17g")
        lock(
            directory,
            "scoring_lock.json",
            [directory / "metrics_by_point.csv", directory / "next_calibration.json"],
            phase=phase,
            status="complete",
            selected_updates=selected,
        )
        print("internal selected", selected, "Q", qualities, flush=True)
        return
    ensemble = load_npz(directory / f"ensemble_e{selected}.npz")
    means = {**basic, **{a: ensemble[a][n:] for a in ARMS}}
    sigmas = {m: np.array(cal["scales"][m]) for m in cfg["methods"]}
    np.savez_compressed(
        directory / "issued_distribution.npz",
        dates=data["dates"][n:],
        **{m + "__mean": v for m, v in means.items()},
        **{m + "__sigma": v for m, v in sigmas.items()},
    )
    lock(
        directory,
        "distribution_lock.json",
        [directory / "issued_distribution.npz"],
        phase=phase,
        forecast_start=n,
        forecast_end=end,
        latest_observed_index=n - 1,
        calibration_indices=cal["error_indices"],
        calibration_source_sha256=sha(root / previous / "next_calibration.json"),
    )
    event(
        root,
        "distribution_locked",
        phase=phase,
        lock_sha256=sha(directory / "distribution_lock.json"),
    )
    observed = labels(cfg, root, end, phase + "_scoring_after_distribution_lock")
    fit_means = dict(
        BPLUS_CONTINUOUS=data["mean"][:n],
        RR_COND=rr[:n],
        **{a: ensemble[a][:n] for a in ARMS},
    )
    metrics, summary = write_scores(
        directory,
        phase,
        observed[n:],
        means,
        sigmas,
        y,
        fit_means,
        data["dates"][n:],
        cfg,
    )
    # Seed distributions share the same pre-issued ensemble calibration scale.
    seed_rows = []
    for arm in ARMS:
        for seed in cfg["neural"]["seeds"]:
            mu = np.load(directory / arm / f"seed_{seed}" / f"e{selected}_mean.npy")[n:]
            seed_rows.extend(
                dict(model=arm, seed=seed, **r)
                for r in scores(observed[n:], mu, sigmas[arm])
            )
    pd.DataFrame(seed_rows).to_csv(
        directory / "seed_metrics.csv", index=False, float_format="%.17g"
    )
    if phase == "development":
        decision = dict(
            time_utc=utc(),
            source_phase=phase,
            selected_updates=selected,
            mean_winner=choose(
                summary, cfg["selection"]["mean_keys"], cfg["methods"], 1e-12
            ),
            probability_winner=choose(
                summary, cfg["selection"]["probability_keys"], cfg["methods"], 1e-12
            ),
            effects=gates(metrics, cfg),
            complete_final_even_if_failed=True,
        )
        write_json(root / "selection.json", decision)
        event(
            root,
            "development_selection_locked",
            mean=decision["mean_winner"],
            probability=decision["probability_winner"],
        )
        next_scales = {
            m: calibrate(observed[1078:1168] - means[m][-90:], cfg).tolist()
            for m in cfg["methods"]
        }
        write_json(
            directory / "next_calibration.json",
            dict(
                time_utc=utc(),
                next_origin=1168,
                error_indices=[1078, 1168],
                last_label_index=1167,
                scales=next_scales,
                source_distribution_lock_sha256=sha(
                    directory / "distribution_lock.json"
                ),
            ),
        )
    else:
        write_json(
            directory / "frozen_selection_evaluation.json",
            dict(
                selection=selection,
                final_scores_of_frozen_winners={
                    k: summary[selection[k]]
                    for k in ("mean_winner", "probability_winner")
                },
                all_method_summary=summary,
                exploratory=True,
            ),
        )
    paths = list(directory.glob("*.csv")) + [directory / "effect_gates.json"]
    paths += [
        p
        for p in (
            directory / "next_calibration.json",
            directory / "frozen_selection_evaluation.json",
        )
        if p.exists()
    ]
    lock(
        directory,
        "scoring_lock.json",
        paths,
        phase=phase,
        status="complete",
        selected_updates=selected,
        neural_fits=len(fits),
        optimizer_updates=sum(f["updates"] for f in fits),
    )
    event(
        root,
        "phase_completed",
        phase=phase,
        mean_rmse={k: v["rmse"] for k, v in summary.items()},
    )
    print(
        json.dumps(
            dict(phase=phase, summary=summary, effect=gates(metrics, cfg)),
            ensure_ascii=False,
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase", choices=["internal", "development", "final_exploratory"]
    )
    args = parser.parse_args()
    cfg = spec()
    root = ROOT / cfg["out"]
    root.mkdir(parents=True, exist_ok=True)
    if not (root / "environment.json").exists():
        write_json(
            root / "environment.json",
            dict(
                time_utc=utc(),
                platform=platform.platform(),
                torch=str(torch.__version__),
                numpy=np.__version__,
                pandas=pd.__version__,
                config=cfg,
                source_guard_count=guard_sources(),
            ),
        )
    try:
        run(args.phase)
    except Exception:
        error = traceback.format_exc()
        event(root, "execution_error", phase=args.phase, traceback=error)
        (
            root / ("error_" + args.phase + "_" + str(time.time_ns()) + ".txt")
        ).write_text(error)
        raise


if __name__ == "__main__":
    main()
