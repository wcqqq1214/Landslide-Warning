"""Chronological base reconstruction, frozen-base correction and full scoring."""

import argparse
import json
import time
import traceback
import numpy as np
import pandas as pd
import torch
from . import core as c

o = c.o


def optimizer_for(model, cfg):
    p = cfg["optimizer"]
    return torch.optim.Adam(
        model.parameters(),
        lr=p["lr"],
        betas=tuple(p["betas"]),
        eps=p["eps"],
        weight_decay=p["weight_decay"],
    )


def train_base(cfg, teachers, y, forcing, r, seed):
    path = c.base_path(cfg, r, seed)
    if r in cfg["correction"]["reused_oof_prefixes"]:
        return
    dest = path.parent
    if (dest / "complete.json").exists():
        o.verify_lock(dest / "complete.json")
        return
    dest.mkdir(parents=True, exist_ok=False)
    yp = y[:r].copy()
    sc = c.old.scaling(c.old.spec(), yp, forcing[:r], teachers[o.teacher_id(r, True)])
    model = c.old.Tide(c.old.spec(), seed)
    optimizer = optimizer_for(model, cfg)
    data = c.base_arrays(teachers, yp, forcing[:r], np.arange(432, r), sc, targets=True)
    schedule = c.old.schedule(c.old.spec(), r, seed)
    np.savez_compressed(dest / "schedule.npz", origins=schedule)
    o.event(c.ROOT / cfg["out"], "base_fit_started", prefix=r, seed=seed, updates=400)
    started = time.monotonic()
    with (dest / "training.jsonl").open("w") as log:
        for step in range(401):
            o.check_deadline(cfg)
            if step:
                optimizer.zero_grad(set_to_none=True)
                loss = c.old.loss(
                    model, tuple(t[schedule[step - 1] - 432] for t in data)
                )
                if not torch.isfinite(loss):
                    raise ArithmeticError("nonfinite historical KIN loss")
                loss.backward()
                grad = sum(
                    p.grad.square().sum()
                    for p in model.parameters()
                    if p.grad is not None
                ).sqrt()
                if not torch.isfinite(grad):
                    raise ArithmeticError("nonfinite historical KIN gradient")
                optimizer.step()
                log.write(
                    json.dumps(
                        dict(step=step, mse=float(loss.detach()), grad_norm=float(grad))
                    )
                    + "\n"
                )
                log.flush()
            if step in cfg["checkpoints"]:
                mu = c.base_predict(model, sc, teachers, yp, forcing[: r + 293], r)
                torch.save(
                    dict(
                        state_dict=model.state_dict(),
                        optimizer_state_dict=optimizer.state_dict(),
                        scaling=sc,
                        seed=seed,
                        arm="TiDE_KIN",
                        step=step,
                        training_prefix=r,
                        config_sha256=o.sha(c.CONFIG),
                        base_config_sha256=o.sha(c.prior.CONFIG),
                    ),
                    dest / f"e{step}.pt",
                )
                np.save(dest / f"e{step}_mean.npy", mu)
    elapsed = time.monotonic() - started
    o.lock(
        dest,
        "complete.json",
        list(dest.glob("*")),
        status="complete",
        kind="historical_base",
        prefix=r,
        seed=seed,
        updates=400,
        elapsed_seconds=elapsed,
    )
    o.event(
        c.ROOT / cfg["out"],
        "base_fit_completed",
        prefix=r,
        seed=seed,
        updates=400,
        elapsed_seconds=elapsed,
    )
    print(f"base prefix={r} seed={seed} complete400", flush=True)


def extend_pool(cfg, teachers, y, forcing, n):
    wanted = c.eligible(cfg, n)
    if not len(wanted):
        return None
    current = c.pool(cfg, n)
    existing = set(current["origins"]) if current is not None else set()
    new = np.array([m for m in wanted if m not in existing], dtype=int)
    if not len(new):
        return current
    assert new[-1] + 293 <= n and len(y) == n
    refs = np.array([c.base_prefix(cfg, int(m)) for m in new])
    for r in sorted(set(refs)):
        for seed in cfg["seeds"]:
            train_base(cfg, teachers, y, forcing, int(r), seed)
    preds = np.zeros((3, len(new), 293, 4), dtype=np.float64)
    hashes = {}
    for r in sorted(set(refs)):
        positions = np.where(refs == r)[0]
        for seed in cfg["seeds"]:
            path = c.base_path(cfg, int(r), seed)
            model, sc, ck = c.base_reload(path)
            assert ck["training_prefix"] == r and sc["fit_prefix"] == r
            hashes[str(path.relative_to(c.ROOT))] = o.sha(path)
            for start in range(0, len(positions), 16):
                ix = positions[start : start + 16]
                ms = new[ix]
                last = int(ms.max())
                a = c.base_arrays(
                    teachers, y[:last], forcing[: last + 293], ms, sc, current=True
                )
                with torch.no_grad():
                    q = model(*a[:2]).numpy()[:, 1:]
                preds[seed, ix] = y[ms - 1, None, :] + q * np.asarray(sc["unit"])
    folder = c.ROOT / cfg["out"] / f"oof/batch_{n}"
    folder.mkdir(parents=True, exist_ok=False)
    np.savez_compressed(
        folder / "paths.npz",
        origins=new,
        base_prefixes=refs,
        seeds=preds,
        raw_cov=c.raw_cov(teachers, new),
    )
    records = [
        dict(
            origin=int(m),
            base_fit_prefix=int(r),
            teacher_fit_prefix=o.teacher_id(int(m), True),
            history_max=int(m - 1),
            target_start=int(m),
            target_end_exclusive=int(m + 293),
            given_driver_end_exclusive=int(m + 293),
            mature_prefix=n,
        )
        for m, r in zip(new, refs)
    ]
    o.write_json(
        folder / "provenance.json",
        dict(
            paths=records,
            base_checkpoint_hashes=hashes,
            reconstructed=True,
            not_actual_historical_publication=True,
        ),
    )
    o.lock(
        folder,
        "lock.json",
        list(folder.glob("*")),
        status="issued_oof",
        mature_prefix=n,
        count=len(new),
        latest_target_exclusive=int(new[-1] + 293),
    )
    o.event(
        c.ROOT / cfg["out"],
        "oof_batch_locked",
        mature_prefix=n,
        count=len(new),
        first=int(new[0]),
        last=int(new[-1]),
        lock_sha256=o.sha(folder / "lock.json"),
    )
    all_paths = c.pool(cfg, n)
    assert np.array_equal(all_paths["origins"], wanted)
    return all_paths


def fit_head(cfg, teachers, y, n, arm, seed, sc, pool, base):
    dest = c.checkpoint_folder(cfg, n, arm, seed)
    if (dest / "complete.json").exists() or (dest / "bootstrap.json").exists():
        o.verify_lock(dest / ("bootstrap.json" if n == 612 else "complete.json"))
        return np.load(dest / f"e{c.final_step(n)}_mean.npy")
    dest.mkdir(parents=True, exist_ok=False)
    model = c.Head(cfg, seed, sc)
    inference = torch.from_numpy(c.head_cov(c.raw_cov(teachers, [n]), sc, arm))
    base_file = c.checkpoint_folder(cfg, n, "TiDE_KIN", seed) / "e400.pt"
    base_sha = o.sha(base_file)
    optimizer = optimizer_for(model, cfg)
    if pool is None:
        assert n == 612 and len(c.eligible(cfg, n)) == 0
        steps = [0]
        o.event(
            c.ROOT / cfg["out"],
            "bootstrap_zero_head",
            origin=n,
            arm=arm,
            seed=seed,
            updates=0,
        )
    else:
        x = torch.from_numpy(c.head_cov(pool["raw_cov"], sc, arm))
        target = torch.from_numpy(
            c.head_targets(cfg, y, pool["origins"], pool["seeds"][seed], sc)
        )
        schedule = c.schedule(cfg, len(pool["origins"]), seed)
        np.savez_compressed(
            dest / "schedule.npz", indices=schedule, origins=pool["origins"][schedule]
        )
        np.save(dest / "training_targets.npy", target.numpy())
        o.write_json(
            dest / "training_support.json",
            dict(
                count=len(pool["origins"]),
                first=int(pool["origins"][0]),
                last=int(pool["origins"][-1]),
                target_max=int(pool["origins"][-1] + 292),
                base_prefixes=sorted(set(map(int, pool["base_prefixes"]))),
                all_full293=True,
                seed_matched=True,
            ),
        )
        steps = range(401)
        o.event(
            c.ROOT / cfg["out"],
            "head_fit_started",
            origin=n,
            arm=arm,
            seed=seed,
            updates=400,
        )
    started = time.monotonic()
    with (dest / "training.jsonl").open("w") as log:
        for step in steps:
            o.check_deadline(cfg)
            if step:
                optimizer.zero_grad(set_to_none=True)
                ix = schedule[step - 1]
                loss = (model(x[ix])[:, 1:] - target[ix]).square().mean()
                if not torch.isfinite(loss):
                    raise ArithmeticError("nonfinite correction loss")
                loss.backward()
                grad = sum(
                    p.grad.square().sum()
                    for p in model.parameters()
                    if p.grad is not None
                ).sqrt()
                if not torch.isfinite(grad):
                    raise ArithmeticError("nonfinite correction gradient")
                optimizer.step()
                log.write(
                    json.dumps(
                        dict(step=step, mse=float(loss.detach()), grad_norm=float(grad))
                    )
                    + "\n"
                )
                log.flush()
            if step in cfg["checkpoints"]:
                with torch.no_grad():
                    raw, actual = model.parts(inference)
                unit = np.asarray(sc["unit"])
                raw = raw.numpy()[0, 1:] * unit
                actual = actual.numpy()[0, 1:] * unit
                mu = base + actual
                assert np.isfinite(mu).all() and np.all(
                    np.abs(actual) <= np.asarray(sc["hydro_cap_mm"]) + 1e-12
                )
                assert o.sha(base_file) == base_sha
                torch.save(
                    dict(
                        state_dict=model.state_dict(),
                        optimizer_state_dict=optimizer.state_dict(),
                        scaling=sc,
                        seed=seed,
                        arm=arm,
                        step=step,
                        training_prefix=n,
                        config_sha256=o.sha(c.CONFIG),
                        base_checkpoint=str(base_file.relative_to(c.ROOT)),
                        base_checkpoint_sha256=base_sha,
                        trainable_parameters=13179,
                        frozen_base_parameters=110392,
                        target="chronological out-of-training KIN residual",
                        bootstrap=pool is None,
                    ),
                    dest / f"e{step}.pt",
                )
                for name, value in [
                    ("mean", mu),
                    ("correction_mm", actual),
                    ("raw_mm", raw),
                    ("base_mm", base),
                ]:
                    np.save(dest / f"e{step}_{name}.npy", value)
    elapsed = time.monotonic() - started
    o.lock(
        dest,
        "bootstrap.json" if pool is None else "complete.json",
        list(dest.glob("*")),
        status="bootstrap_zero" if pool is None else "complete",
        kind="correction_head",
        origin=n,
        arm=arm,
        seed=seed,
        updates=0 if pool is None else 400,
        elapsed_seconds=elapsed,
    )
    if pool is not None:
        o.event(
            c.ROOT / cfg["out"],
            "head_fit_completed",
            origin=n,
            arm=arm,
            seed=seed,
            updates=400,
            elapsed_seconds=elapsed,
        )
    print(
        f"head origin={n} {arm} seed={seed} complete{0 if pool is None else 400}",
        flush=True,
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
        y = o.labels(cfg, n, "current_prefix_for_mature_oof_correction_and_calibration")
        forcing, _ = o.read_forcing(c.ROOT / cfg["data"], end)
        pool = extend_pool(cfg, teachers, y, forcing[:n], n)
        sc = c.scaling(cfg, y, forcing[:n], teachers[o.teacher_id(n, True)])
        o.write_json(folder / "scaling.json", sc)
        source = c.ROOT / cfg["prior_controls_out"] / f"origin_{n}"
        o.verify_lock(source / "issue_lock.json")
        pm, ps = o.load_npz(source / "means.npz"), o.load_npz(source / "seeds.npz")
        means = {k: pm[k] for k in cfg["controls"] + cfg["reuse_arms"]}
        seeds = {k: ps[k] for k in cfg["controls"] + cfg["reuse_arms"]}
        for arm in cfg["new_arms"]:
            seeds[arm] = np.stack(
                [
                    fit_head(
                        cfg, teachers, y, n, arm, s, sc, pool, seeds["TiDE_KIN"][s]
                    )
                    for s in cfg["seeds"]
                ]
            )
            means[arm] = seeds[arm].mean(0)
        np.savez_compressed(folder / "means.npz", **means)
        np.savez_compressed(folder / "seeds.npz", **seeds)
        paths = [folder / f for f in ["means.npz", "seeds.npz", "scaling.json"]]
        if previous is not None:
            pf = root / f"origin_{previous}"
            o.verify_lock(pf / "issue_lock.json")
            issued = o.load_npz(pf / "means.npz")
            errors = {
                m: y[previous + 90 : previous + 180] - issued[m][90:180] for m in means
            }
            assert (
                all(v.shape == (90, 4) for v in errors.values()) and previous + 180 <= n
            )
            sigmas = {
                m: np.maximum(
                    np.sqrt(np.mean(v * v, axis=0)),
                    cfg["calibration"]["sigma_floor_mm"],
                )
                for m, v in errors.items()
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
                for f in ["calibration_errors.npz", "sigmas.npz", "calibration.json"]
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
    b = list(root.glob("historical_bases/fit_*/seed_*/complete.json"))
    h = list(root.glob("origin_*/*/seed_*/complete.json"))
    z = list(root.glob("origin_*/*/seed_*/bootstrap.json"))
    assert len(b) == 21 and len(h) == 18 and len(z) == 6
    assert sum(o.read_json(p)["updates"] for p in b + h) == 15600
    o.lock(
        root,
        "training_complete.json",
        b
        + h
        + z
        + [root / f"origin_{n}/issue_lock.json" for n in cfg["origins"]]
        + list(root.glob("oof/batch_*/lock.json")),
        status="complete",
        new_fits=39,
        new_base_fits=21,
        new_head_fits=18,
        bootstrap_heads=6,
        updates=15600,
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
            if p["candidate"] == "TiDE_HCAL" and p["reference"] == reference
        )
        for reference in ["TiDE_CAL", "TiDE_KIN"]
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
                            / f"e{c.final_step(n)}_{key}.npy"
                        )
                        for seed in cfg["seeds"]
                    ]
                )
                for key in ["base_mm", "raw_mm", "correction_mm"]
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
                        selected["raw_mm"][:, point_index],
                        selected["correction_mm"][:, point_index],
                    )
                    contribution_rows.append(
                        dict(
                            origin=n,
                            method=arm,
                            seed=identity,
                            point=point,
                            cap_mm=cap[point_index],
                            base_abs_mean=float(
                                np.abs(selected["base_mm"][:, point_index]).mean()
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
        stable_hcal_vs_references=stable,
        contribution_rows=len(contribution_rows),
        reused_fits=12,
        reused_checkpoints=60,
        new_checkpoints=201,
        new_base_fits=21,
        new_head_fits=18,
        bootstrap_heads=6,
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
