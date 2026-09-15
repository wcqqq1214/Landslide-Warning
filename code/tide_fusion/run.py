"""Frozen paired training; issue every full path before full-label scoring."""

import argparse
import json
import time
import traceback
import numpy as np
import pandas as pd
import torch
from . import core as c

o = c.o


def fit_one(cfg, teachers, y, forcing, n, end, arm, seed, sc, dataset):
    root = c.ROOT / cfg["out"]
    dest = root / f"origin_{n}" / arm / f"seed_{seed}"
    if (dest / "complete.json").exists():
        o.verify_lock(dest / "complete.json")
        return np.load(dest / "e400_mean.npy")
    dest.mkdir(parents=True, exist_ok=False)
    model = c.make_model(cfg, seed, arm, sc)
    opt = cfg["optimizer"]
    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=opt["lr"],
        betas=tuple(opt["betas"]),
        eps=opt["eps"],
        weight_decay=opt["weight_decay"],
    )
    ms = c.schedule(cfg, n, seed)
    np.savez_compressed(dest / "schedule.npz", origins=ms)
    o.event(root, "fit_started", origin=n, arm=arm, seed=seed, updates=cfg["updates"])
    started = time.monotonic()
    with (dest / "training.jsonl").open("w") as log:
        for step in range(cfg["updates"] + 1):
            o.check_deadline(cfg)
            if step:
                model.train()
                optimizer.zero_grad(set_to_none=True)
                batch = tuple(
                    t[ms[step - 1] - cfg["training"]["first_origin"]] for t in dataset
                )
                loss = c.loss(model, batch)
                if not torch.isfinite(loss):
                    raise ArithmeticError("Nonfinite neural objective")
                loss.backward()
                grad = float(
                    sum(
                        p.grad.square().sum()
                        for p in model.parameters()
                        if p.grad is not None
                    ).sqrt()
                )
                if not np.isfinite(grad):
                    raise ArithmeticError("Nonfinite neural gradient")
                optimizer.step()
                log.write(
                    json.dumps(
                        dict(step=step, mse=float(loss.detach()), grad_norm=grad),
                        allow_nan=False,
                    )
                    + "\n"
                )
                log.flush()
            if step in cfg["checkpoints"]:
                mu, change = c.predict(
                    cfg, model, teachers, y, forcing, n, end, sc, arm
                )
                torch.save(
                    dict(
                        state_dict=model.state_dict(),
                        optimizer_state_dict=optimizer.state_dict(),
                        scaling=sc,
                        seed=seed,
                        arm=arm,
                        step=step,
                        training_prefix=n,
                        target="direct origin increment",
                        config_sha256=o.sha(c.CONFIG),
                    ),
                    dest / f"e{step}.pt",
                )
                np.save(dest / f"e{step}_mean.npy", mu)
                np.save(dest / f"e{step}_increment_mm.npy", change)
                for name, value in c.components(
                    cfg, model, teachers, y, forcing, n, sc, arm
                ).items():
                    np.save(dest / f"e{step}_{name}_mm.npy", value)
                print(f"origin={n} {arm} seed={seed} update={step}", flush=True)
    elapsed = time.monotonic() - started
    o.lock(
        dest,
        "complete.json",
        list(dest.glob("*")),
        status="complete",
        new_fits=1,
        updates=cfg["updates"],
        elapsed_seconds=elapsed,
    )
    o.event(
        root,
        "fit_completed",
        origin=n,
        arm=arm,
        seed=seed,
        updates=cfg["updates"],
        elapsed_seconds=elapsed,
    )
    return mu


def train():
    cfg = c.spec()
    o.setup(cfg)
    c.guard()
    root = c.ROOT / cfg["out"]
    teachers = o.bank(cfg)
    previous = None
    for n, end in zip(cfg["origins"], cfg["ends"]):
        folder = root / f"origin_{n}"
        if (folder / "issue_lock.json").exists():
            o.verify_lock(folder / "issue_lock.json")
            previous = n
            continue
        folder.mkdir(parents=True, exist_ok=True)
        y = o.labels(cfg, n, "neural_fit_prefix_and_mature_previous_errors")
        forcing, _ = o.read_forcing(c.ROOT / cfg["data"], end)
        sc = c.scaling(cfg, y, forcing[:n], teachers[o.teacher_id(n, True)])
        prior_scale = o.read_json(
            c.ROOT / cfg["prior_tide_out"] / f"origin_{n}/scaling.json"
        )
        assert {k: v for k, v in sc.items() if k != "hydro_cap_mm"} == prior_scale
        o.write_json(folder / "scaling.json", sc)
        o.event(root, "old_endpoints_reused", origin=n, methods=cfg["reuse_arms"])
        prior = c.ROOT / cfg["prior_controls_out"] / f"origin_{n}"
        o.verify_lock(prior / "issue_lock.json")
        pm, ps = o.load_npz(prior / "means.npz"), o.load_npz(prior / "seeds.npz")
        means = {k: pm[k] for k in cfg["controls"] + cfg["reuse_arms"]}
        seeds = {k: ps[k] for k in cfg["controls"] + cfg["reuse_arms"]}
        for arm in cfg["new_arms"]:
            dataset = c.arrays(
                cfg, teachers, y, forcing[:n], np.arange(432, n), sc, arm, targets=True
            )
            seeds[arm] = np.stack(
                [
                    fit_one(cfg, teachers, y, forcing, n, end, arm, s, sc, dataset)
                    for s in cfg["seeds"]
                ]
            )
            means[arm] = seeds[arm].mean(0)
            del dataset
        np.savez_compressed(folder / "means.npz", **means)
        np.savez_compressed(folder / "seeds.npz", **seeds)
        paths = [folder / f for f in ("means.npz", "seeds.npz", "scaling.json")]
        if previous is not None:
            pf = root / f"origin_{previous}"
            o.verify_lock(pf / "issue_lock.json")
            issued = o.load_npz(pf / "means.npz")
            assert previous + 180 <= n
            errors = {
                m: y[previous + 90 : previous + 180] - issued[m][90:180] for m in means
            }
            assert all(e.shape == (90, 4) for e in errors.values())
            sigmas = {
                m: np.maximum(
                    np.sqrt(np.mean(e * e, axis=0)),
                    cfg["calibration"]["sigma_floor_mm"],
                )
                for m, e in errors.items()
            }
            np.savez_compressed(folder / "calibration_errors.npz", **errors)
            np.savez_compressed(folder / "sigmas.npz", **sigmas)
            o.write_json(
                folder / "calibration.json",
                dict(
                    previous_origin=previous,
                    start=previous + 90,
                    end=previous + 180,
                    matured_before=n,
                    count=90,
                    gap=n - previous - 180,
                ),
            )
            paths += [
                folder / f
                for f in ("calibration_errors.npz", "sigmas.npz", "calibration.json")
            ]
        o.lock(
            folder,
            "issue_lock.json",
            paths,
            status="bootstrap" if previous is None else "issued",
            origin=n,
            end=end,
            displacement_feedback=False,
        )
        o.event(
            root,
            "trajectory_issued",
            origin=n,
            end=end,
            lock_sha256=o.sha(folder / "issue_lock.json"),
        )
        previous = n
    done = [o.read_json(p) for p in root.glob("origin_*/*/seed_*/complete.json")]
    assert (
        len(done) == cfg["max_new_fits"]
        and sum(d["updates"] for d in done) == cfg["max_optimizer_updates"]
    )
    o.lock(
        root,
        "training_complete.json",
        [root / f"origin_{n}/issue_lock.json" for n in cfg["origins"]]
        + list(root.glob("origin_*/*/seed_*/complete.json")),
        status="complete",
        new_fits=len(done),
        updates=sum(d["updates"] for d in done),
        new_bplus_fits=0,
        new_physical_forwards=0,
    )


def score():
    cfg = c.spec()
    c.guard()
    root = c.ROOT / cfg["out"]
    o.verify_lock(root / "training_complete.json")
    for n in cfg["origins"]:
        o.verify_lock(root / f"origin_{n}/issue_lock.json")
    if (root / "analysis_lock.json").exists():
        o.verify_lock(root / "analysis_lock.json")
        return
    y = o.labels(cfg, 1461, "all_issued_before_full_scoring")
    _, dates = o.read_forcing(c.ROOT / cfg["data"], 1461)
    out = root / "analysis"
    out.mkdir(exist_ok=False)
    rows = []
    summaries = []
    sr = []
    sp = []
    daily = []
    pairs = []
    endpoints = []
    differences = []
    for n, end in list(zip(cfg["origins"], cfg["ends"]))[1:]:
        folder = root / f"origin_{n}"
        means = o.load_npz(folder / "means.npz")
        seeds = o.load_npz(folder / "seeds.npz")
        sigmas = o.load_npz(folder / "sigmas.npz")
        by = {}
        for method, mu in means.items():
            by[method] = o.scores(y[n:end], mu, sigmas[method])
            rows.extend(dict(origin=n, method=method, **r) for r in by[method])
            summaries.append(dict(origin=n, method=method, **o.summarize(by[method])))
            for seed, sm in enumerate(seeds[method]):
                values = o.scores(y[n:end], sm, sigmas[method])
                sr.append(
                    dict(origin=n, method=method, seed=seed, **o.summarize(values))
                )
                sp.extend(dict(origin=n, method=method, seed=seed, **v) for v in values)
            for ident, sm in [("ensemble", mu)] + [
                (str(i), v) for i, v in enumerate(seeds[method])
            ]:
                for h in cfg["reporting"]["boundary_diagnostic_horizons"]:
                    for p, point in enumerate(cfg["points"]):
                        e = float(sm[h - 1, p] - y[n + h - 1, p])
                        endpoints.append(
                            dict(
                                origin=n,
                                method=method,
                                seed=ident,
                                horizon=h,
                                point=point,
                                error=e,
                                absolute_error=abs(e),
                                squared_error=e * e,
                            )
                        )
            for h in range(293):
                for p, point in enumerate(cfg["points"]):
                    daily.append(
                        dict(
                            origin=n,
                            issue_date=dates[n - 1],
                            target_index=n + h,
                            date=dates[n + h],
                            distance=h + 1,
                            method=method,
                            point=point,
                            observed=y[n + h, p],
                            mean=mu[h, p],
                            sigma=sigmas[method][p],
                        )
                    )
        comparisons = [(a, b) for a in cfg["arms"] for b in cfg["controls"]] + cfg[
            "reporting"
        ]["paired_edges"]
        for a, b in comparisons:
            flags = []
            for seed in cfg["seeds"]:
                va = o.summarize(o.scores(y[n:end], seeds[a][seed]))
                vb = o.summarize(o.scores(y[n:end], seeds[b][seed]))
                flags.append(all(va[k] < vb[k] for k in ("mae", "rmse")))
            pairs.append(
                dict(
                    origin=n,
                    candidate=a,
                    reference=b,
                    seed_flags=flags,
                    seed_both_improve=sum(flags),
                    seed_agreement_pass=sum(flags)
                    >= cfg["effect"]["minimum_seed_agreement"],
                    **o.effect(by[a], by[b], cfg),
                )
            )
        for ident, group in [("ensemble", means)] + [
            (str(seed), {k: v[seed] for k, v in seeds.items()}) for seed in cfg["seeds"]
        ]:
            metrics = {
                arm: o.scores(y[n:end], group[arm], sigmas[arm]) for arm in cfg["arms"]
            }
            for point, p in [("average", None)] + [
                (name, i) for i, name in enumerate(cfg["points"])
            ]:
                vals = {
                    a: o.summarize(metrics[a]) if p is None else metrics[a][p]
                    for a in cfg["arms"]
                }
                for metric in (
                    "mae",
                    "rmse",
                    "crps",
                    "interval_score90",
                    "coverage90",
                    "width90",
                ):
                    raw = {a: vals[a][metric] for a in cfg["arms"]}
                    factors = {
                        a + "__minus__" + b: raw[a] - raw[b]
                        for a, b in cfg["reporting"]["paired_edges"]
                    }
                    for factor, value in factors.items():
                        differences.append(
                            dict(
                                origin=n,
                                seed=ident,
                                point=point,
                                metric=metric,
                                factor=factor,
                                effect=float(value),
                                **{a: raw[a] for a in cfg["arms"]},
                            )
                        )
    for name, values in [
        ("metrics_by_point", rows),
        ("phase_summary", summaries),
        ("seed_summary", sr),
        ("seed_metrics_by_point", sp),
        ("daily_predictions", daily),
        ("endpoint_errors", endpoints),
        ("paired_metric_effects", differences),
    ]:
        pd.DataFrame(values).to_csv(
            out / f"{name}.csv", index=False, float_format="%.17g"
        )
    o.write_json(out / "pairing.json", pairs)
    stable = {
        reference: all(
            p["mean_pass"] and p["seed_agreement_pass"]
            for p in pairs
            if p["candidate"] == "TiDE_BOUND" and p["reference"] == reference
        )
        for reference in ["TiDE_SPLIT", "TiDE_KIN", "TiDE_PHYS"]
    }
    contribution_rows = []
    for n in cfg["origins"]:
        sc = o.read_json(root / f"origin_{n}/scaling.json")
        cap = np.asarray(sc["hydro_cap_mm"])
        for arm in cfg["new_arms"]:
            values = {
                key: np.stack(
                    [
                        np.load(
                            c.checkpoint_folder(cfg, n, arm, seed)
                            / f"e400_{key}_mm.npy"
                        )
                        for seed in cfg["seeds"]
                    ]
                )
                for key in ["main", "hydro_raw", "hydro"]
            }
            for identity in ["ensemble", "0", "1", "2"]:
                selected = {
                    key: value.mean(0)
                    if identity == "ensemble"
                    else value[int(identity)]
                    for key, value in values.items()
                }
                for point_index, point in enumerate(cfg["points"]):
                    raw, actual = (
                        selected["hydro_raw"][:, point_index],
                        selected["hydro"][:, point_index],
                    )
                    contribution_rows.append(
                        dict(
                            origin=n,
                            method=arm,
                            seed=identity,
                            point=point,
                            cap_mm=cap[point_index],
                            main_abs_mean=float(
                                np.abs(selected["main"][:, point_index]).mean()
                            ),
                            raw_abs_mean=float(np.abs(raw).mean()),
                            raw_abs_max=float(np.abs(raw).max()),
                            hydro_abs_mean=float(np.abs(actual).mean()),
                            hydro_abs_max=float(np.abs(actual).max()),
                            raw_over_cap_fraction=float(
                                np.mean(np.abs(raw) > cap[point_index])
                            ),
                            hydro_over95cap_fraction=float(
                                np.mean(np.abs(actual) >= 0.95 * cap[point_index])
                            ),
                        )
                    )
    pd.DataFrame(contribution_rows).to_csv(
        out / "component_summary.csv", index=False, float_format="%.17g"
    )
    bplus = {
        a: [p for p in pairs if p["candidate"] == a and p["reference"] == c.B]
        for a in cfg["arms"]
    }
    outcome = dict(
        all_prescribed_windows_complete=True,
        new_fits=cfg["max_new_fits"],
        updates=cfg["max_optimizer_updates"],
        new_bplus_fits=0,
        summary_rows=len(summaries),
        point_rows=len(rows),
        seed_rows=len(sr),
        daily_rows=len(daily),
        endpoint_rows=len(endpoints),
        stable_bound_vs_references=stable,
        contribution_rows=len(contribution_rows),
        reused_fits=24,
        reused_checkpoints=120,
        new_checkpoints=120,
        paired_metric_rows=len(differences),
        joint_bplus_pass_counts={
            a: sum(p["joint_pass"] for p in ps) for a, ps in bplus.items()
        },
        replacement_goal_met=any(
            all(p["joint_pass"] for p in ps) for ps in bplus.values()
        ),
        selection=False,
    )
    o.write_json(out / "outcome.json", outcome)
    o.lock(root, "analysis_lock.json", list(out.glob("*")), status="complete")
    o.event(root, "scoring_complete", **outcome)
    print(
        pd.DataFrame(summaries)[["origin", "method", "mae", "rmse", "crps"]].to_string(
            index=False
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["train", "score"])
    args = parser.parse_args()
    try:
        train() if args.command == "train" else score()
    except Exception:
        o.write_json(
            c.ROOT / c.spec()["out"] / f"error_{args.command}_{int(time.time())}.json",
            dict(time_utc=o.utc(), error=traceback.format_exc()),
        )
        raise
