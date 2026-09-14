"""Finite paired training, chronological issue locks, and deferred full scoring."""

import argparse
import json
import time
import traceback

import numpy as np
import pandas as pd
import torch

from .core import (
    B,
    CONFIG,
    ROOT,
    OriginModel,
    bank,
    check_deadline,
    current_teacher,
    draw_schedule,
    effect,
    event,
    inputs,
    labels,
    load_npz,
    lock,
    predict,
    read_forcing,
    read_json,
    scaling,
    scores,
    setup,
    sha,
    spec,
    summarize,
    targets,
    utc,
    verify_implementation,
    verify_lock,
    write_json,
)


def fit_one(cfg, teachers, y, n, end, arm, seed, scale):
    root = ROOT / cfg["out"]
    dest = root / f"origin_{n}" / arm / f"seed_{seed}"
    if (dest / "complete.json").exists():
        verify_lock(dest / "complete.json")
        return np.load(dest / "e200_mean.npy")
    dest.mkdir(parents=True, exist_ok=False)
    model = OriginModel(cfg, seed, arm)
    oc = cfg["optimizer"]
    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=oc["lr"],
        betas=tuple(oc["betas"]),
        eps=oc["eps"],
        weight_decay=0,
    )
    ms, hs = draw_schedule(n, seed, cfg)
    np.savez_compressed(dest / "schedule.npz", origins=ms, horizons=hs)
    event(root, "fit_started", origin=n, arm=arm, seed=seed, updates=cfg["updates"])
    start = time.monotonic()
    with (dest / "training.jsonl").open("w") as log:
        for step in range(cfg["updates"] + 1):
            check_deadline(cfg)
            if step:
                model.train()
                optimizer.zero_grad(set_to_none=True)
                x = inputs(teachers, y, ms[step - 1], hs[step - 1], scale)
                target = targets(teachers, y, ms[step - 1], hs[step - 1], scale)
                output = model(*x)
                mse = (output - target).square().mean()
                penalty = output.square().mean()
                loss = mse + cfg["residual_penalty"] * penalty
                if not torch.isfinite(loss):
                    raise ArithmeticError("Nonfinite loss")
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
                            mse=float(mse.detach()),
                            penalty=float(penalty.detach()),
                            loss=float(loss.detach()),
                            grad_norm=grad,
                        ),
                        allow_nan=False,
                    )
                    + "\n"
                )
                log.flush()
            if step in cfg["checkpoints"]:
                mean = predict(model, teachers, y, n, end, scale)
                torch.save(
                    dict(
                        state_dict=model.state_dict(),
                        scaling=scale,
                        seed=seed,
                        arm=arm,
                        step=step,
                        training_prefix=n,
                        config_sha256=sha(CONFIG),
                    ),
                    dest / f"e{step}.pt",
                )
                np.save(dest / f"e{step}_mean.npy", mean)
                print(f"origin={n} {arm} seed={seed} update={step}", flush=True)
    event(
        root,
        "fit_completed",
        origin=n,
        arm=arm,
        seed=seed,
        updates=cfg["updates"],
        elapsed_seconds=time.monotonic() - start,
    )
    lock(
        dest,
        "complete.json",
        list(dest.glob("*")),
        status="complete",
        new_fits=1,
        updates=cfg["updates"],
        elapsed_seconds=time.monotonic() - start,
    )
    return mean


def controls(cfg, n, end):
    old = ROOT / cfg["prior_out"] / f"origin_{n}"
    a = load_npz(old / "alpha_means.npz")
    s = load_npz(old / "alpha_seeds.npz")
    prior_lambda = load_npz(old / "lambda_means.npz")
    ls = load_npz(old / "lambda_seeds.npz")
    mapping = {
        B: B,
        "DRIFT1": "DRIFT1",
        "RR_COND": "RR_COND",
        "OLD_TRANSFORMER": "A1",
        "OLD_HALF": "A05",
    }
    means = {k: a[v][n:end].copy() for k, v in mapping.items()}
    seeds = {k: s[v][:, n:end].copy() for k, v in mapping.items()}
    means["OLD_REG1"] = prior_lambda["L1"][n:end].copy()
    seeds["OLD_REG1"] = ls["L1"][:, n:end].copy()
    return means, seeds


def train():
    cfg = spec()
    setup(cfg)
    verify_implementation(cfg)
    root = ROOT / cfg["out"]
    teachers = bank(cfg)
    previous = None
    for n, end in zip(cfg["origins"], cfg["ends"]):
        dest = root / f"origin_{n}"
        if (dest / "issue_lock.json").exists():
            verify_lock(dest / "issue_lock.json")
            previous = n
            continue
        dest.mkdir(parents=True, exist_ok=True)
        y = labels(cfg, n, "fit_current_prefix_and_calibrate_previous_issued_errors")
        scale = scaling(current_teacher(teachers, n), y, cfg)
        write_json(dest / "scaling.json", scale)
        means, seeds = controls(cfg, n, end)
        for arm in cfg["arms"]:
            seeds[arm] = np.stack(
                [fit_one(cfg, teachers, y, n, end, arm, s, scale) for s in cfg["seeds"]]
            )
            means[arm] = seeds[arm].mean(0)
        np.savez_compressed(dest / "means.npz", **means)
        np.savez_compressed(dest / "seeds.npz", **seeds)
        issue_paths = [dest / "means.npz", dest / "seeds.npz", dest / "scaling.json"]
        if previous is not None:
            verify_lock(root / f"origin_{previous}/issue_lock.json")
            prior = load_npz(root / f"origin_{previous}/means.npz")
            errors = {
                k: y[previous + 90 : previous + 180] - prior[k][90:180] for k in means
            }
            sigma = {
                k: np.maximum(
                    np.sqrt(np.mean(v * v, axis=0)),
                    cfg["calibration"]["sigma_floor_mm"],
                )
                for k, v in errors.items()
            }
            assert all(e.shape == (90, 4) for e in errors.values())
            np.savez_compressed(dest / "sigmas.npz", **sigma)
            np.savez_compressed(dest / "calibration_errors.npz", **errors)
            write_json(
                dest / "calibration.json",
                dict(
                    previous_origin=previous,
                    start=previous + 90,
                    end=previous + 180,
                    labels_mature_before=n,
                    selection="none",
                    count=90,
                ),
            )
            issue_paths += [
                dest / "sigmas.npz",
                dest / "calibration_errors.npz",
                dest / "calibration.json",
            ]
        lock(
            dest,
            "issue_lock.json",
            issue_paths,
            status="bootstrap" if previous is None else "issued",
            origin=n,
            end=end,
            trained_updates=200,
            displacement_feedback=False,
        )
        event(
            root,
            "trajectory_issued",
            origin=n,
            end=end,
            bootstrap=previous is None,
            lock_sha256=sha(dest / "issue_lock.json"),
        )
        previous = n
    lock(
        root,
        "training_complete.json",
        [root / f"origin_{n}/issue_lock.json" for n in cfg["origins"]],
        status="complete",
        new_fits=36,
        updates=7200,
    )


def score():
    cfg = spec()
    root = ROOT / cfg["out"]
    verify_implementation(cfg)
    verify_lock(root / "training_complete.json")
    if (root / "scoring_lock.json").exists():
        verify_lock(root / "scoring_lock.json")
        return
    for n in cfg["origins"]:
        verify_lock(root / f"origin_{n}/issue_lock.json")
    y = labels(cfg, 1461, "all_issued_before_outer_scoring")
    _, dates = read_forcing(ROOT / cfg["data"], 1461)
    out = root / "analysis"
    out.mkdir(exist_ok=False)
    rows = []
    summary = []
    seedrows = []
    daily = []
    gates = {}
    pairings = []
    for n, end in list(zip(cfg["origins"], cfg["ends"]))[1:]:
        dest = root / f"origin_{n}"
        means = load_npz(dest / "means.npz")
        seeds = load_npz(dest / "seeds.npz")
        sigma = load_npz(dest / "sigmas.npz")
        phase = "final_exploratory" if n == 1168 else "historical_exploratory"
        metrics = {}
        for method, mu in means.items():
            metrics[method] = scores(y[n:end], mu, sigma[method])
            rows.extend(
                dict(origin=n, phase=phase, method=method, **r) for r in metrics[method]
            )
            summary.append(
                dict(origin=n, phase=phase, method=method, **summarize(metrics[method]))
            )
            for s, sm in enumerate(seeds[method]):
                seedrows.append(
                    dict(
                        origin=n,
                        phase=phase,
                        method=method,
                        seed=s,
                        **summarize(scores(y[n:end], sm, sigma[method])),
                    )
                )
            for h in range(end - n):
                for p, point in enumerate(cfg["points"]):
                    daily.append(
                        dict(
                            origin=n,
                            issue_date=dates[n],
                            target_index=n + h,
                            date=dates[n + h],
                            distance=h + 1,
                            method=method,
                            point=point,
                            observed=y[n + h, p],
                            mean=mu[h, p],
                            sigma=sigma[method][p],
                        )
                    )
        gates[str(n)] = {k: effect(v, metrics[B], cfg) for k, v in metrics.items()}
        for arm, reference in [(a, B) for a in cfg["arms"]] + [
            ("COND_ATTN", "NO_OBS_ATTN"),
            ("COND_ATTN", "POOL_MLP"),
        ]:
            flags = []
            for s in cfg["seeds"]:
                c = summarize(scores(y[n:end], seeds[arm][s]))
                r = summarize(scores(y[n:end], seeds[reference][s]))
                flags.append(all(c[k] < r[k] for k in ["mae", "rmse"]))
            pairings.append(
                dict(
                    origin=n,
                    candidate=arm,
                    reference=reference,
                    seed_flags=flags,
                    seed_both_improve=sum(flags),
                    **effect(metrics[arm], metrics[reference], cfg),
                )
            )
    for name, data in [
        ("metrics_by_point", rows),
        ("phase_summary", summary),
        ("seed_summary", seedrows),
        ("daily_predictions", daily),
    ]:
        pd.DataFrame(data).to_csv(
            out / f"{name}.csv", index=False, float_format="%.17g"
        )
    write_json(out / "effect_gates.json", gates)
    write_json(out / "pairing.json", pairings)
    done = [read_json(p) for p in root.glob("origin_*/*/seed_*/complete.json")]
    assert len(done) == 36 and sum(d["updates"] for d in done) == 7200
    conclusions = {
        a: dict(
            historical_mean_pass=[
                n for n in cfg["origins"][1:-1] if gates[str(n)][a]["mean_pass"]
            ],
            final=gates["1168"][a],
        )
        for a in cfg["arms"]
    }
    write_json(
        out / "outcome.json",
        dict(
            all_prescribed_windows_complete=True,
            new_fits=36,
            updates=7200,
            summary_rows=len(summary),
            point_rows=len(rows),
            seed_rows=len(seedrows),
            daily_rows=len(daily),
            conclusions=conclusions,
        ),
    )
    lock(root, "scoring_lock.json", list(out.glob("*")), status="complete")
    event(root, "scoring_complete", summary_rows=len(summary))
    print(
        pd.DataFrame(summary)[["origin", "method", "mae", "rmse", "crps"]].to_string(
            index=False
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["train", "score"])
    args = parser.parse_args()
    try:
        train() if args.phase == "train" else score()
    except Exception:
        root = ROOT / spec()["out"]
        root.mkdir(exist_ok=True, parents=True)
        write_json(
            root / f"error_{args.phase}_{time.time_ns()}.json",
            dict(time_utc=utc(), traceback=traceback.format_exc()),
        )
        raise


if __name__ == "__main__":
    main()
