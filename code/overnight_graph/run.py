"""Fixed chronological fits and deferred complete scoring; no model selection."""

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
    GraphGRU,
    bank,
    check_deadline,
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
    target_bank,
    targets,
    teacher_id,
    utc,
    verify_implementation,
    verify_lock,
    write_json,
)


def fit_one(cfg, teachers, y, cache, n, end, phase, arm, seed, sc):
    root = ROOT / cfg["out"]
    dest = root / phase / f"origin_{n}" / arm / f"seed_{seed}"
    if (dest / "complete.json").exists():
        verify_lock(dest / "complete.json")
        return np.load(dest / "e200_mean.npy")
    dest.mkdir(parents=True, exist_ok=False)
    model = GraphGRU(cfg, seed, arm)
    optimizer = torch.optim.Adam(
        model.parameters(), lr=0.001, betas=(0.9, 0.999), eps=1e-8, weight_decay=0
    )
    ms, hs = draw_schedule(n, seed, cfg)
    np.savez_compressed(dest / "schedule.npz", origins=ms, horizons=hs)
    event(root, "fit_started", phase=phase, origin=n, arm=arm, seed=seed, updates=200)
    started = time.monotonic()
    with (dest / "training.jsonl").open("w") as log:
        for step in range(201):
            check_deadline(cfg)
            if step:
                model.train()
                optimizer.zero_grad(set_to_none=True)
                output = model(*inputs(teachers, y, ms[step - 1], hs[step - 1], sc))
                target = targets(cache, ms[step - 1], hs[step - 1], sc, model.dual)
                mse = (output.sum(-1) - target.sum(-1)).square().mean()
                penalty = output.sum(-1).square().mean()
                auxiliary = (
                    (output - target).square().mean(0).mean(0).mean(0).sum()
                    if model.dual
                    else torch.tensor(0.0, dtype=torch.float64)
                )
                loss = mse + penalty + 0.5 * auxiliary
                if not torch.isfinite(loss):
                    raise ArithmeticError("nonfinite loss")
                loss.backward()
                grad = float(
                    sum(
                        p.grad.square().sum()
                        for p in model.parameters()
                        if p.grad is not None
                    ).sqrt()
                )
                if not np.isfinite(grad):
                    raise ArithmeticError("nonfinite gradient")
                optimizer.step()
                log.write(
                    json.dumps(
                        dict(
                            step=step,
                            mse=float(mse.detach()),
                            penalty=float(penalty.detach()),
                            auxiliary=float(auxiliary.detach()),
                            loss=float(loss.detach()),
                            grad_norm=grad,
                        ),
                        allow_nan=False,
                    )
                    + "\n"
                )
                log.flush()
            if step in cfg["checkpoints"]:
                mean, components = predict(model, teachers, y, n, end, sc)
                torch.save(
                    dict(
                        state_dict=model.state_dict(),
                        scaling=sc,
                        arm=arm,
                        seed=seed,
                        step=step,
                        training_prefix=n,
                        phase=phase,
                        config_sha256=sha(CONFIG),
                    ),
                    dest / f"e{step}.pt",
                )
                np.save(dest / f"e{step}_mean.npy", mean)
                np.save(dest / f"e{step}_components.npy", components)
                print(f"{phase} origin={n} {arm} seed={seed} update={step}", flush=True)
    elapsed = time.monotonic() - started
    event(
        root,
        "fit_completed",
        phase=phase,
        origin=n,
        arm=arm,
        seed=seed,
        updates=200,
        elapsed_seconds=elapsed,
    )
    lock(
        dest,
        "complete.json",
        list(dest.glob("*")),
        status="complete",
        new_fits=1,
        updates=200,
        elapsed_seconds=elapsed,
    )
    return mean


def train(phase):
    cfg = spec()
    setup(cfg)
    verify_implementation(cfg)
    root = ROOT / cfg["out"]
    teachers = bank(cfg)
    if phase == "dual":
        assert read_json(root / "diagnostic/decision.json")["triggered"]
    arms = cfg["arms"] if phase == "base" else [cfg["conditional_arm"]]
    previous = None
    for n, end in zip(cfg["origins"], cfg["ends"]):
        folder = root / phase / f"origin_{n}"
        if (folder / "issue_lock.json").exists():
            verify_lock(folder / "issue_lock.json")
            previous = n
            continue
        folder.mkdir(parents=True, exist_ok=True)
        y = labels(
            cfg, n, f"{phase}_fit_current_prefix_and_calibrate_previous_issued_errors"
        )
        sc = scaling(teachers[teacher_id(n, True)], y, cfg)
        write_json(folder / "scaling.json", sc)
        cache = target_bank(teachers, y)
        means, seeds = {}, {}
        if phase == "base":
            old = ROOT / cfg["prior_out"] / f"origin_{n}"
            means = load_npz(old / "means.npz")
            seeds = load_npz(old / "seeds.npz")
            assert set(means) == set(cfg["controls"])
        for arm in arms:
            seeds[arm] = np.stack(
                [
                    fit_one(cfg, teachers, y, cache, n, end, phase, arm, s, sc)
                    for s in cfg["seeds"]
                ]
            )
            means[arm] = seeds[arm].mean(0)
        np.savez_compressed(folder / "means.npz", **means)
        np.savez_compressed(folder / "seeds.npz", **seeds)
        files = [folder / "means.npz", folder / "seeds.npz", folder / "scaling.json"]
        if previous is not None:
            verify_lock(root / phase / f"origin_{previous}/issue_lock.json")
            old = load_npz(root / phase / f"origin_{previous}/means.npz")
            errors = {
                m: y[previous + 90 : previous + 180] - old[m][90:180] for m in means
            }
            sigmas = {
                m: np.maximum(np.sqrt((e * e).mean(0)), 1e-6) for m, e in errors.items()
            }
            assert all(e.shape == (90, 4) for e in errors.values())
            np.savez_compressed(folder / "calibration_errors.npz", **errors)
            np.savez_compressed(folder / "sigmas.npz", **sigmas)
            write_json(
                folder / "calibration.json",
                dict(
                    previous_origin=previous,
                    start=previous + 90,
                    end=previous + 180,
                    matured_before=n,
                    count=90,
                ),
            )
            files += [
                folder / f
                for f in ["calibration_errors.npz", "sigmas.npz", "calibration.json"]
            ]
        lock(
            folder,
            "issue_lock.json",
            files,
            status="bootstrap" if previous is None else "issued",
            phase=phase,
            origin=n,
            end=end,
            displacement_feedback=False,
        )
        event(
            root,
            "trajectory_issued",
            phase=phase,
            origin=n,
            end=end,
            lock_sha256=sha(folder / "issue_lock.json"),
        )
        previous = n
    done = [
        read_json(p) for p in (root / phase).glob("origin_*/*/seed_*/complete.json")
    ]
    assert len(done) == 4 * 3 * len(arms)
    lock(
        root / phase,
        "training_complete.json",
        [root / phase / f"origin_{n}/issue_lock.json" for n in cfg["origins"]],
        status="complete",
        new_fits=len(done),
        updates=sum(d["updates"] for d in done),
    )


def score(final):
    cfg = spec()
    verify_implementation(cfg)
    root = ROOT / cfg["out"]
    phases = ["base"]
    if final and read_json(root / "diagnostic/decision.json")["triggered"]:
        phases += ["dual"]
    for phase in phases:
        verify_lock(root / phase / "training_complete.json")
        for n in cfg["origins"]:
            verify_lock(root / phase / f"origin_{n}/issue_lock.json")
    name = "analysis" if final else "analysis_base"
    if (root / (name + "_lock.json")).exists():
        verify_lock(root / (name + "_lock.json"))
        return
    y = labels(cfg, 1461, name + "_all_issued_before_full_scoring")
    _, dates = read_forcing(ROOT / cfg["data"], 1461)
    out = root / name
    out.mkdir(exist_ok=False)
    rows, summary, seedrows, daily, pairings = [], [], [], [], []
    gates = {}
    for n, end in list(zip(cfg["origins"], cfg["ends"]))[1:]:
        means, seeds, sigma = {}, {}, {}
        for phase in phases:
            folder = root / phase / f"origin_{n}"
            means.update(load_npz(folder / "means.npz"))
            seeds.update(load_npz(folder / "seeds.npz"))
            sigma.update(load_npz(folder / "sigmas.npz"))
        metrics = {}
        for m, mu in means.items():
            metrics[m] = scores(y[n:end], mu, sigma[m])
            rows += [dict(origin=n, method=m, **r) for r in metrics[m]]
            summary.append(dict(origin=n, method=m, **summarize(metrics[m])))
            for s, sm in enumerate(seeds[m]):
                seedrows.append(
                    dict(
                        origin=n,
                        method=m,
                        seed=s,
                        **summarize(scores(y[n:end], sm, sigma[m])),
                    )
                )
            for h in range(293):
                for j, p in enumerate(cfg["points"]):
                    daily.append(
                        dict(
                            origin=n,
                            issue_date=dates[n],
                            target_index=n + h,
                            date=dates[n + h],
                            distance=h + 1,
                            method=m,
                            point=p,
                            observed=y[n + h, j],
                            mean=mu[h, j],
                            sigma=sigma[m][j],
                        )
                    )
        gates[str(n)] = {m: effect(v, metrics[B], cfg) for m, v in metrics.items()}
        arms = cfg["arms"] + ([cfg["conditional_arm"]] if "dual" in phases else [])
        pairs = [(a, B) for a in arms] + [("GRU_GRAPH", "GRU_LOCAL")]
        if "dual" in phases:
            pairs += [(cfg["conditional_arm"], "GRU_GRAPH")]
        for a, b in pairs:
            flags = []
            for seed in cfg["seeds"]:
                ca = summarize(scores(y[n:end], seeds[a][seed]))
                cb = summarize(scores(y[n:end], seeds[b][seed]))
                flags.append(all(ca[k] < cb[k] for k in ["mae", "rmse"]))
            pairings.append(
                dict(
                    origin=n,
                    candidate=a,
                    reference=b,
                    seed_flags=flags,
                    seed_both_improve=sum(flags),
                    **effect(metrics[a], metrics[b], cfg),
                )
            )
    for file, data in [
        ("metrics_by_point", rows),
        ("phase_summary", summary),
        ("seed_summary", seedrows),
        ("daily_predictions", daily),
    ]:
        pd.DataFrame(data).to_csv(
            out / (file + ".csv"), index=False, float_format="%.17g"
        )
    write_json(out / "effect_gates.json", gates)
    write_json(out / "pairing.json", pairings)
    write_json(
        out / "outcome.json",
        dict(
            all_prescribed_windows_complete=True,
            phases=phases,
            summary_rows=len(summary),
            point_rows=len(rows),
            seed_rows=len(seedrows),
            daily_rows=len(daily),
        ),
    )
    lock(root, name + "_lock.json", list(out.glob("*")), status="complete")
    event(root, "scoring_complete", analysis=name, summary_rows=len(summary))
    print(
        pd.DataFrame(summary)[["origin", "method", "mae", "rmse", "crps"]].to_string(
            index=False
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["train-base", "score-base", "train-dual", "score-final"]
    )
    command = parser.parse_args().command
    try:
        if command.startswith("train"):
            train(command.split("-")[1])
        else:
            score(command == "score-final")
    except Exception:
        write_json(
            ROOT / spec()["out"] / f"error_{command}_{int(time.time())}.json",
            dict(time_utc=utc(), error=traceback.format_exc()),
        )
        raise
