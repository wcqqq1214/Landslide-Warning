"""Issue complete trajectories before releasing outer labels for scoring."""

import argparse
import json
import time
import traceback

import numpy as np
import pandas as pd
import torch

from .core import (
    ALPHAS,
    ARM,
    B,
    BASES,
    CONFIG,
    ROOT,
    TrajectoryModel,
    check_deadline,
    drift,
    effect,
    event,
    fit_ridge,
    labels,
    load_npz,
    lock,
    objective,
    predict,
    read_forcing,
    read_json,
    reload_model,
    ridge_predict,
    saved_source,
    scores,
    select_and_calibrate,
    sha,
    shrink,
    spec,
    summarize,
    teacher_path,
    training_inputs,
    utc,
    verify_implementation,
    verify_lock,
    write_json,
)


def fit_one(cfg, n, key, seed, data, y):
    root = ROOT / cfg["out"]
    dest = root / f"origin_{n}" / key / f"seed_{seed}"
    if (dest / "complete.json").exists():
        verify_lock(dest / "complete.json")
        return np.load(dest / "e400_mean.npy")
    dest.mkdir(parents=True, exist_ok=False)
    scale, x, target = training_inputs(data, y, cfg)
    strength = cfg["lambdas"][key]
    source = saved_source(cfg, n, key, seed)
    started = time.monotonic()
    if source is not None:
        model, oldscale, saved = reload_model(source, cfg)
        assert oldscale.state == scale.state
        assert saved["training_prefix"] == n and saved["step"] == 400
        if key == "L1":
            assert saved["regularization_lambda"] == strength
        mean = predict(model, scale, ARM, data["x"], data["mean"])
        prior = np.load(source.with_name("e400_mean.npy"))
        overlap = min(len(prior), len(mean))
        np.testing.assert_allclose(mean[:overlap], prior[:overlap], atol=1e-9, rtol=0)
        np.save(dest / "e400_mean.npy", mean)
        write_json(
            dest / "reused.json",
            dict(
                source=str(source.relative_to(ROOT)),
                source_sha256=sha(source),
                training_prefix=n,
                seed=seed,
                strength=strength,
                mean_overlap_max_abs=float(
                    np.max(abs(mean[:overlap] - prior[:overlap]))
                ),
            ),
        )
        event(root, "model_reused", origin=n, key=key, seed=seed, updates=0)
        new_fits, updates = 0, 0
    else:
        model = TrajectoryModel(cfg, seed, ARM)
        nc = cfg["neural"]
        optimizer = torch.optim.Adam(
            model.parameters(),
            lr=nc["lr"],
            betas=tuple(nc["betas"]),
            eps=nc["eps"],
            weight_decay=0,
        )
        event(root, "fit_started", origin=n, key=key, seed=seed, updates=cfg["updates"])
        with (dest / "training.jsonl").open("w") as log:
            for step in range(cfg["updates"] + 1):
                check_deadline(cfg)
                if step:
                    model.train()
                    optimizer.zero_grad(set_to_none=True)
                    total, mse, penalty = objective(model(x), target, strength)
                    total.backward()
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
                                data=float(mse.detach()),
                                penalty=float(penalty.detach()),
                                total=float(total.detach()),
                                grad_norm=grad,
                            ),
                            allow_nan=False,
                        )
                        + "\n"
                    )
                    log.flush()
                if step in [0] + nc["checkpoints"]:
                    mean = predict(model, scale, ARM, data["x"], data["mean"])
                    torch.save(
                        dict(
                            state_dict=model.state_dict(),
                            scaling=scale.state,
                            seed=seed,
                            arm=ARM,
                            step=step,
                            training_prefix=n,
                            regularization_lambda=strength,
                            config_sha256=sha(CONFIG),
                        ),
                        dest / f"e{step}.pt",
                    )
                    np.save(dest / f"e{step}_mean.npy", mean)
                    print(f"origin={n} {key} seed={seed} update={step}", flush=True)
        new_fits, updates = 1, cfg["updates"]
        event(
            root,
            "fit_completed",
            origin=n,
            key=key,
            seed=seed,
            updates=updates,
            elapsed_seconds=time.monotonic() - started,
        )
    lock(
        dest,
        "complete.json",
        list(dest.glob("*")),
        status="complete",
        origin=n,
        key=key,
        seed=seed,
        new_fits=new_fits,
        updates=updates,
        elapsed_seconds=time.monotonic() - started,
    )
    return mean


def issue(cfg, n, previous, phase, means, seed_means, y, order):
    root = ROOT / cfg["out"]
    dest = root / f"origin_{n}"
    # Candidate means are locked before any selection / calibration calculations.
    np.savez_compressed(dest / f"{phase}_candidate_means.npz", **means)
    np.savez_compressed(dest / f"{phase}_candidate_seeds.npz", **seed_means)
    lock(
        dest,
        f"{phase}_candidate_lock.json",
        [dest / f"{phase}_candidate_means.npz", dest / f"{phase}_candidate_seeds.npz"],
        origin=n,
        training_last_index=n - 1,
    )
    event(
        root,
        "candidate_means_locked",
        origin=n,
        phase=phase,
        lock_sha256=sha(dest / f"{phase}_candidate_lock.json"),
    )
    if previous is None:
        lock(
            dest,
            f"{phase}_issue_lock.json",
            [dest / f"{phase}_candidate_lock.json"],
            status="bootstrap_complete",
            origin=n,
        )
        event(root, "trajectory_issued", origin=n, phase=phase, bootstrap=True)
        return
    prevdir = root / f"origin_{previous}"
    verify_lock(prevdir / f"{phase}_issue_lock.json")
    prior = load_npz(prevdir / f"{phase}_candidate_means.npz")
    record, sigma, errors = select_and_calibrate(prior, y, previous, n, order, cfg)
    chosen = record["selected"]
    alias = "ALPHA_SELECTED" if phase == "alpha" else "LAMBDA_SELECTED"
    means[alias] = means[chosen].copy()
    seed_means[alias] = seed_means[chosen].copy()
    sigma[alias] = sigma[chosen].copy()
    errors[alias] = errors[chosen].copy()
    write_json(dest / f"{phase}_selection.json", record)
    np.savez_compressed(dest / f"{phase}_means.npz", **means)
    np.savez_compressed(dest / f"{phase}_seeds.npz", **seed_means)
    np.savez_compressed(dest / f"{phase}_sigmas.npz", **sigma)
    np.savez_compressed(dest / f"{phase}_calibration_errors.npz", **errors)
    lock(
        dest,
        f"{phase}_issue_lock.json",
        [
            dest / f"{phase}_{suffix}"
            for suffix in [
                "candidate_lock.json",
                "selection.json",
                "means.npz",
                "seeds.npz",
                "sigmas.npz",
                "calibration_errors.npz",
            ]
        ],
        status="issued",
        origin=n,
        selected=chosen,
        last_calibration_label=record["last_label_index"],
        displacement_feedback=False,
    )
    event(
        root,
        "trajectory_issued",
        origin=n,
        phase=phase,
        selected=chosen,
        lock_sha256=sha(dest / f"{phase}_issue_lock.json"),
    )
    print(
        f"{phase}: origin={n} selected={chosen}; complete trajectory locked", flush=True
    )


def run_phase(phase):
    cfg = spec()
    verify_implementation(cfg)
    check_deadline(cfg)
    root = ROOT / cfg["out"]
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    previous = None
    for n, end in zip(cfg["origins"], cfg["ends"]):
        dest = root / f"origin_{n}"
        if (dest / f"{phase}_issue_lock.json").exists():
            verify_lock(dest / f"{phase}_issue_lock.json")
            previous = n
            continue
        if phase == "lambda":
            verify_lock(dest / "alpha_issue_lock.json")
        data = load_npz(teacher_path(cfg, n))
        assert data["x"].shape == (end, 22) and data["mean"].shape == (end, 4)
        assert data["teacher_fit_prefix"].item() <= n
        y = labels(cfg, root, n, f"{phase}_origin_{n}_training_selection_calibration")
        scale, _, _ = training_inputs(data, y, cfg)
        dest.mkdir(exist_ok=True, parents=True)
        means, seeds = {}, {}
        if phase == "alpha":
            write_json(dest / "scaling.json", scale.state)
            seeds["L0"] = np.stack(
                [fit_one(cfg, n, "L0", s, data, y) for s in cfg["neural"]["seeds"]]
            )
            original = seeds["L0"].mean(0)
            means[B] = data["mean"].copy()
            means["DRIFT1"] = np.concatenate([y, drift(y, end - n)])
            model = fit_ridge(data["x"][:n], y, scale, cfg["ridge"]["alpha"])
            np.savez_compressed(dest / "ridge.npz", **model)
            means["RR_COND"] = ridge_predict(model, data["x"])
            event(root, "ridge_fitted", origin=n, training_rows=n)
            for key in BASES:
                seeds[key] = np.repeat(means[key][None], 3, axis=0)
            for key, weight in ALPHAS.items():
                seeds[key] = np.stack(
                    [shrink(data["mean"], a, weight) for a in seeds["L0"]]
                )
                means[key] = seeds[key].mean(0)
            del seeds["L0"]
            # Compute exactly from the ensemble too, retaining the seed-first primary convention.
            for key, weight in ALPHAS.items():
                np.testing.assert_allclose(
                    means[key],
                    shrink(data["mean"], original, weight),
                    atol=1e-10,
                    rtol=0,
                )
            means["A0"] = means[B].copy()
            order = cfg["selection"]["alpha_order"]
        else:
            existing = load_npz(dest / "alpha_candidate_seeds.npz")
            seeds["L0"] = existing["A1"]
            means["L0"] = seeds["L0"].mean(0)
            for key in ["L033", "L1", "L3"]:
                seeds[key] = np.stack(
                    [fit_one(cfg, n, key, s, data, y) for s in cfg["neural"]["seeds"]]
                )
                means[key] = seeds[key].mean(0)
            order = cfg["selection"]["lambda_order"]
        issue(cfg, n, previous, phase, means, seeds, y, order)
        previous = n
    lock(
        root,
        f"{phase}_complete.json",
        [root / f"origin_{n}/{phase}_issue_lock.json" for n in cfg["origins"]],
        status="complete",
        phase=phase,
        full_outer_scores_read=False,
    )


def score_all():
    cfg = spec()
    verify_implementation(cfg)
    root = ROOT / cfg["out"]
    verify_lock(root / "alpha_complete.json")
    verify_lock(root / "lambda_complete.json")
    if (root / "scoring_lock.json").exists():
        verify_lock(root / "scoring_lock.json")
        return
    for n in cfg["origins"]:
        for phase in ["alpha", "lambda"]:
            verify_lock(root / f"origin_{n}/{phase}_issue_lock.json")
    event(root, "all_trajectories_locked_before_outer_scoring")
    y = labels(cfg, root, 1461, "outer_scoring_after_every_alpha_lambda_issue_lock")
    _, dates = read_forcing(ROOT / cfg["data"], 1461)
    output = root / "analysis"
    output.mkdir(exist_ok=True)
    rows, summary_rows, seed_rows, daily, gates_all, pairing = [], [], [], [], {}, []
    for n, end in list(zip(cfg["origins"], cfg["ends"]))[1:]:
        dest = root / f"origin_{n}"
        means, sigma, seeds = {}, {}, {}
        for phase in ["alpha", "lambda"]:
            means.update(load_npz(dest / f"{phase}_means.npz"))
            sigma.update(load_npz(dest / f"{phase}_sigmas.npz"))
            seeds.update(load_npz(dest / f"{phase}_seeds.npz"))
        metrics = {}
        label = "final_exploratory" if n == 1168 else "historical_exploratory"
        for key, full in means.items():
            mu, sd = full[n:end], sigma[key]
            metrics[key] = scores(y[n:end], mu, sd)
            rows.extend(
                dict(origin=n, phase=label, method=key, **r) for r in metrics[key]
            )
            summary_rows.append(
                dict(origin=n, phase=label, method=key, **summarize(metrics[key]))
            )
            for s, seed_mean in enumerate(seeds[key]):
                # Ensemble-based frozen sigma is shared: no seed-specific probability selection.
                seed_rows.append(
                    dict(
                        origin=n,
                        phase=label,
                        method=key,
                        seed=s,
                        **summarize(scores(y[n:end], seed_mean[n:end], sd)),
                    )
                )
            for j in range(end - n):
                for p, point in enumerate(cfg["points"]):
                    daily.append(
                        dict(
                            origin=n,
                            issue_date=dates[n],
                            target_index=n + j,
                            date=dates[n + j],
                            distance=j + 1,
                            method=key,
                            point=point,
                            observed=y[n + j, p],
                            mean=mu[j, p],
                            sigma=sd[p],
                        )
                    )
        gates_all[str(n)] = {}
        for key in means:
            gates_all[str(n)][key] = effect(metrics[key], metrics[B], cfg)
        for candidate, reference in [
            ("ALPHA_SELECTED", B),
            ("LAMBDA_SELECTED", B),
            ("LAMBDA_SELECTED", "ALPHA_SELECTED"),
        ]:
            flags = []
            for s in range(3):
                c = summarize(scores(y[n:end], seeds[candidate][s, n:end]))
                r = summarize(scores(y[n:end], seeds[reference][s, n:end]))
                flags.append(all(c[k] < r[k] for k in ["mae", "rmse"]))
            pairing.append(
                dict(
                    origin=n,
                    candidate=candidate,
                    reference=reference,
                    seed_both_improve=sum(flags),
                    seed_flags=flags,
                    **effect(metrics[candidate], metrics[reference], cfg),
                )
            )
    for filename, values in [
        ("metrics_by_point", rows),
        ("phase_summary", summary_rows),
        ("seed_summary", seed_rows),
        ("daily_predictions", daily),
    ]:
        pd.DataFrame(values).to_csv(
            output / f"{filename}.csv", index=False, float_format="%.17g"
        )
    write_json(output / "effect_gates.json", gates_all)
    write_json(output / "pairing.json", pairing)
    historical = cfg["origins"][1:-1]
    conclusion = {}
    for key in ["ALPHA_SELECTED", "LAMBDA_SELECTED"]:
        pass_origins = [
            n
            for n in historical
            if gates_all[str(n)][key]["mean_pass"]
            and next(
                r["seed_both_improve"]
                for r in pairing
                if r["origin"] == n and r["candidate"] == key and r["reference"] == B
            )
            >= 2
        ]
        conclusion[key] = dict(
            historical_mean_pass_origins=pass_origins,
            stable_mean_support=len(pass_origins) == len(historical),
            final=gates_all["1168"][key],
        )
    receipts = [read_json(p) for p in root.glob("origin_*/L*/seed_*/complete.json")]
    fits = sum(r["new_fits"] for r in receipts)
    updates = sum(r["updates"] for r in receipts)
    assert fits == cfg["max_new_fits"] and updates == cfg["max_optimizer_updates"]
    assert len(receipts) == 60
    write_json(
        output / "outcome.json",
        dict(
            conclusions=conclusion,
            formal_new_fits=fits,
            optimizer_updates=updates,
            reused_fits=len(receipts) - fits,
            ridge_fits=5,
            all_prescribed_windows_complete=True,
            distributions=len(summary_rows),
            daily_rows=len(daily),
            rl_run=False,
        ),
    )
    lock(
        root,
        "scoring_lock.json",
        list(output.glob("*")),
        status="complete",
        formal_new_fits=fits,
        optimizer_updates=updates,
    )
    event(root, "all_outer_scoring_complete", distributions=len(summary_rows))
    print(
        json.dumps(read_json(output / "outcome.json"), ensure_ascii=False), flush=True
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["alpha", "lambda", "score"])
    args = parser.parse_args()
    try:
        if args.phase == "score":
            score_all()
        else:
            run_phase(args.phase)
    except Exception:
        cfg = spec()
        p = ROOT / cfg["out"] / f"error_{args.phase}_{time.time_ns()}.json"
        write_json(p, dict(time=utc(), traceback=traceback.format_exc()))
        raise


if __name__ == "__main__":
    main()
