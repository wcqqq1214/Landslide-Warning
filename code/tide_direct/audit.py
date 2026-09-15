"""Independent data construction, dense prediction, calibration and score audit."""

import argparse
import json
import math
import traceback
import numpy as np
import pandas as pd
import torch
from transformer_temporal.audit import independent_scores, independent_gate
from . import core as c
from .numpy_model import forward


def independent_input(cfg, teachers, y, forcing, m, sc, arm, limit):
    """Construct one point/time input without production feature/input functions."""
    length = 180
    slots = 294
    unit = np.asarray(sc["unit"])
    hist = np.stack([(y[m - length : m, p] - y[m - 1, p]) / unit[p] for p in range(4)])
    indices = list(range(m - length, m)) + list(range(m - 1, m + 293))
    cov = np.zeros((4, length + slots, 12))
    t = teachers[c.o.teacher_id(m, m == len(y))] if arm == "TiDE_PHYS" else None
    if t is not None:
        assert int(t["teacher_fit_prefix"]) <= m
    for j, tidx in enumerate(indices):
        cov[:, j, 10] = (tidx - m + 1) / 293
        if tidx >= limit:
            continue
        cov[:, j, 11] = 1.0
        rain, level = forcing[tidx]
        drv = np.array(
            [
                rain,
                level,
                level - forcing[max(0, tidx - 1), 1],
                sum(forcing[max(0, tidx - 6) : tidx + 1, 0]),
                sum(forcing[max(0, tidx - 29) : tidx + 1, 0]),
            ]
        )
        cov[:, j, :5] = (drv - sc["driver_mean"]) / sc["driver_std"]
        if t is not None:
            for p in range(4):
                phys = np.array(
                    [
                        t["mean"][tidx, p] - t["y0"][p],
                        t["mean"][tidx, p] - t["mean"][max(0, tidx - 1), p],
                        t["moisture"][tidx, p],
                        t["rain_head"][tidx, p],
                        t["reservoir_head"][tidx],
                    ]
                )
                cov[p, j, 5:10] = (
                    phys - np.asarray(sc["physics_mean"])[p]
                ) / np.asarray(sc["physics_std"])[p]
    return hist, cov


def main(attempt):
    cfg = c.spec()
    o = c.o
    o.setup(cfg)
    source_count = c.guard()
    root = c.ROOT / cfg["out"]
    out = root / attempt
    out.mkdir(exist_ok=False)
    checks = []
    receipt = dict(status="running", source_files=source_count, max_difference=0.0)

    def close(name, a, b, tol=1e-8):
        a, b = np.asarray(a), np.asarray(b)
        assert a.shape == b.shape and np.isfinite(a).all() and np.isfinite(b).all(), (
            name
        )
        diff = float(np.max(abs(a - b), initial=0))
        assert diff <= tol, (name, diff, tol)
        checks.append(
            dict(name=name, values=int(a.size), max_difference=diff, tolerance=tol)
        )
        receipt["max_difference"] = max(receipt["max_difference"], diff)

    try:
        train = o.verify_lock(root / "training_complete.json")
        o.verify_lock(root / "analysis_lock.json")
        assert (
            train["new_fits"] == 24
            and train["updates"] == 9600
            and train["new_bplus_fits"] == 0
            and train["new_physical_forwards"] == 0
        )
        teachers = o.bank(cfg)
        y = o.read_labels(c.ROOT / cfg["data"], 1461)
        forcing, dates = o.read_forcing(c.ROOT / cfg["data"], 1461)
        all_means = {}
        all_seeds = {}
        all_sigmas = {}
        models = 0
        full_targets = 0
        for n, end in zip(cfg["origins"], cfg["ends"]):
            folder = root / f"origin_{n}"
            o.verify_lock(folder / "issue_lock.json")
            sc = o.read_json(folder / "scaling.json")
            scu = np.asarray(sc["unit"])
            close(f"n{n} raw displacement unit", scu, np.maximum(y[:n].std(0), 1.0), 0)
            drv = np.column_stack(
                [
                    forcing[:n, 0],
                    forcing[:n, 1],
                    forcing[:n, 1] - np.r_[forcing[0, 1], forcing[: n - 1, 1]],
                    [forcing[max(0, i - 6) : i + 1, 0].sum() for i in range(n)],
                    [forcing[max(0, i - 29) : i + 1, 0].sum() for i in range(n)],
                ]
            )
            close(f"n{n} driver mean", sc["driver_mean"], drv.mean(0))
            close(f"n{n} driver std", sc["driver_std"], np.maximum(drv.std(0), 1e-6))
            t = teachers[o.teacher_id(n, True)]
            phys = np.stack(
                [
                    t["mean"][:n] - t["y0"],
                    np.diff(t["mean"][:n], axis=0, prepend=t["mean"][:1]),
                    t["moisture"][:n],
                    t["rain_head"][:n],
                    np.repeat(t["reservoir_head"][:n, None], 4, axis=1),
                ],
                axis=-1,
            )
            close(f"n{n} physics mean", sc["physics_mean"], phys.mean(0))
            close(f"n{n} physics std", sc["physics_std"], np.maximum(phys.std(0), 1e-6))
            all_means[n] = o.load_npz(folder / "means.npz")
            all_seeds[n] = o.load_npz(folder / "seeds.npz")
            old = c.ROOT / cfg["prior_controls_out"] / f"origin_{n}"
            for name, group in [("means", all_means[n]), ("seeds", all_seeds[n])]:
                prior = o.load_npz(old / f"{name}.npz")
                for method in cfg["controls"]:
                    close(
                        f"n{n} copied {name}/{method}", group[method], prior[method], 0
                    )
            starts = np.arange(432, n)
            for arm in cfg["arms"]:
                dataset = c.arrays(
                    cfg, teachers, y[:n], forcing[:n], starts, sc, arm, targets=True
                )
                # All sampled origins are drawn from this independently verified complete bank.
                for i, m in enumerate(starts):
                    h, x = independent_input(
                        cfg, teachers, y[:n], forcing[:n], int(m), sc, arm, n
                    )
                    close(f"{n}/{arm}/m{m}/history", dataset[0][i], h, 1e-10)
                    close(f"{n}/{arm}/m{m}/covariates", dataset[1][i], x, 1e-8)
                    mature = min(293, n - m)
                    target = np.zeros((293, 4))
                    mask = np.zeros(293)
                    target[:mature] = (y[m : m + mature] - y[m - 1]) / scu
                    mask[:mature] = 1
                    close(f"{n}/{arm}/m{m}/target", dataset[2][i], target, 0)
                    close(f"{n}/{arm}/m{m}/mask", dataset[3][i], mask, 0)
                    full_targets += int(mature == 293)
                ih, ix = independent_input(
                    cfg, teachers, y[:n], forcing[:end], n, sc, arm, end
                )
                final = []
                for seed in cfg["seeds"]:
                    dest = folder / arm / f"seed_{seed}"
                    done = o.verify_lock(dest / "complete.json")
                    assert done["updates"] == 400 and done["new_fits"] == 1
                    actual = o.load_npz(dest / "schedule.npz")["origins"]
                    expected = np.random.default_rng(seed).integers(
                        432, n, size=(400, 8)
                    )
                    close(f"{n}/{arm}/s{seed}/schedule", actual, expected, 0)
                    logs = [
                        json.loads(line)
                        for line in (dest / "training.jsonl").read_text().splitlines()
                    ]
                    assert [r["step"] for r in logs] == list(range(1, 401))
                    assert all(
                        math.isfinite(r[k]) for r in logs for k in ("mse", "grad_norm")
                    )
                    batch = tuple(a[actual[0] - 432] for a in dataset)
                    zero_loss = np.mean(
                        [
                            (batch[2][i].numpy() ** 2).mean(-1).sum()
                            / batch[3][i].sum().item()
                            for i in range(8)
                        ]
                    )
                    close(
                        f"{n}/{arm}/s{seed}/first loss",
                        logs[0]["mse"],
                        zero_loss,
                        1e-10,
                    )
                    for step in cfg["checkpoints"]:
                        path = dest / f"e{step}.pt"
                        model, cs, ck = c.reload(path)
                        assert (
                            ck["step"] == step
                            and ck["training_prefix"] == n
                            and ck["arm"] == arm
                            and ck["config_sha256"] == o.sha(c.CONFIG)
                        )
                        assert (
                            cs == sc
                            and sum(p.numel() for p in model.parameters()) == 110392
                        )
                        assert all(torch.isfinite(p).all() for p in model.parameters())
                        if step:
                            assert all(
                                int(v["step"]) == step
                                for v in ck["optimizer_state_dict"]["state"].values()
                            )
                        else:
                            original = c.Tide(cfg, seed).state_dict()
                            for name, value in ck["state_dict"].items():
                                close(
                                    f"{n}/{arm}/s{seed}/init/{name}",
                                    value,
                                    original[name],
                                    0,
                                )
                        q = forward(ck["state_dict"], ih[None], ix[None])[0]
                        close(f"{n}/{arm}/s{seed}/e{step}/anchor", q[0], np.zeros(4), 0)
                        inc = q[1:] * scu
                        mu = y[n - 1] + inc
                        close(
                            f"{n}/{arm}/s{seed}/e{step}/numpy mean",
                            np.load(dest / f"e{step}_mean.npy"),
                            mu,
                            1e-7,
                        )
                        close(
                            f"{n}/{arm}/s{seed}/e{step}/numpy increment",
                            np.load(dest / f"e{step}_increment_mm.npy"),
                            inc,
                            1e-7,
                        )
                        replay = c.predict(
                            cfg, model, teachers, y[:n], forcing[:end], n, end, sc, arm
                        )[0]
                        close(
                            f"{n}/{arm}/s{seed}/e{step}/torch reload",
                            np.load(dest / f"e{step}_mean.npy"),
                            replay,
                            1e-9,
                        )
                        models += 1
                    final.append(np.load(dest / "e400_mean.npy"))
                close(f"n{n}/{arm}/seed stack", all_seeds[n][arm], np.stack(final), 0)
                close(
                    f"n{n}/{arm}/ensemble",
                    all_means[n][arm],
                    np.stack(final).mean(0),
                    0,
                )
                del dataset
            if n != 612:
                previous = cfg["origins"][cfg["origins"].index(n) - 1]
                errors = o.load_npz(folder / "calibration_errors.npz")
                sigmas = o.load_npz(folder / "sigmas.npz")
                all_sigmas[n] = sigmas
                record = o.read_json(folder / "calibration.json")
                assert record == dict(
                    previous_origin=previous,
                    start=previous + 90,
                    end=previous + 180,
                    matured_before=n,
                    count=90,
                    gap=n - previous - 180,
                )
                assert previous + 180 <= n
                old_sigmas = o.load_npz(old / "sigmas.npz")
                for method in cfg["controls"] + cfg["arms"]:
                    e = (
                        y[previous + 90 : previous + 180]
                        - all_means[previous][method][90:180]
                    )
                    close(f"n{n}/{method}/mature pool", errors[method], e, 0)
                    close(
                        f"n{n}/{method}/sigma",
                        sigmas[method],
                        np.maximum((np.square(e).sum(0) / 90) ** 0.5, 1e-6),
                        1e-10,
                    )
                    if method in cfg["controls"]:
                        close(
                            f"n{n}/{method}/copied old sigma",
                            sigmas[method],
                            old_sigmas[method],
                            0,
                        )
        assert models == 120

        def read(name):
            return pd.read_csv(
                root / "analysis" / f"{name}.csv", float_precision="round_trip"
            )

        point = read("metrics_by_point").set_index(["origin", "method", "point"])
        summary = read("phase_summary").set_index(["origin", "method"])
        seed_points = read("seed_metrics_by_point").set_index(
            ["origin", "method", "seed", "point"]
        )
        seed_summary = read("seed_summary").set_index(["origin", "method", "seed"])
        independently = {}
        independent_seed = {}
        for n, end in list(zip(cfg["origins"], cfg["ends"]))[1:]:
            for method in cfg["controls"] + cfg["arms"]:
                v = independent_scores(
                    y[n:end], all_means[n][method], all_sigmas[n][method]
                )
                independently[n, method] = v
                for k, values in v.items():
                    close(
                        f"{n}/{method}/point/{k}",
                        [point.loc[(n, method, p), k] for p in cfg["points"]],
                        values,
                    )
                    close(
                        f"{n}/{method}/summary/{k}",
                        summary.loc[(n, method), k],
                        values.mean(),
                    )
                for seed in cfg["seeds"]:
                    v = independent_scores(
                        y[n:end], all_seeds[n][method][seed], all_sigmas[n][method]
                    )
                    independent_seed[n, method, seed] = v
                    for k, values in v.items():
                        close(
                            f"{n}/{method}/s{seed}/{k}",
                            [
                                seed_points.loc[(n, method, seed, p), k]
                                for p in cfg["points"]
                            ],
                            values,
                        )
                        close(
                            f"{n}/{method}/s{seed}/average/{k}",
                            seed_summary.loc[(n, method, seed), k],
                            values.mean(),
                        )
        pairs = o.read_json(root / "analysis/pairing.json")
        assert len(pairs) == 33
        for p in pairs:
            n, a, b = p["origin"], p["candidate"], p["reference"]
            expected = independent_gate(independently[n, a], independently[n, b], cfg)
            for k, v in expected.items():
                assert p[k] == v, (n, a, b, k)
            flags = [
                all(
                    independent_seed[n, a, s][k].mean()
                    < independent_seed[n, b, s][k].mean()
                    for k in ("mae", "rmse")
                )
                for s in cfg["seeds"]
            ]
            assert (
                p["seed_flags"] == flags
                and p["seed_both_improve"] == sum(flags)
                and p["seed_agreement_pass"] == (sum(flags) >= 2)
            )
        daily = read("daily_predictions")
        assert len(daily) == 24612
        for (n, m, p), group in daily.groupby(
            ["origin", "method", "point"], sort=False
        ):
            pi = cfg["points"].index(p)
            group = group.sort_values("distance")
            close(
                f"{n}/{m}/{p}/daily target index",
                group.target_index,
                np.arange(n, n + 293),
                0,
            )
            close(f"{n}/{m}/{p}/daily horizon", group.distance, np.arange(1, 294), 0)
            assert list(group.date) == list(dates[n : n + 293]) and set(
                group.issue_date
            ) == {dates[n - 1]}
            close(f"{n}/{m}/{p}/daily observed", group.observed, y[n : n + 293, pi], 0)
            close(f"{n}/{m}/{p}/daily mean", group["mean"], all_means[n][m][:, pi], 0)
            close(
                f"{n}/{m}/{p}/daily sigma",
                group.sigma,
                np.repeat(all_sigmas[n][m][pi], 293),
                0,
            )
        endpoints = read("endpoint_errors")
        assert len(endpoints) == 1008
        for r in endpoints.to_dict("records"):
            n, m, p, h = (
                r["origin"],
                r["method"],
                cfg["points"].index(r["point"]),
                r["horizon"],
            )
            mu = (
                all_means[n][m]
                if str(r["seed"]) == "ensemble"
                else all_seeds[n][m][int(r["seed"])]
            )
            e = mu[h - 1, p] - y[n + h - 1, p]
            close(
                f"endpoint/{n}/{m}/{p}/{h}/{r['seed']}",
                [r["error"], r["absolute_error"], r["squared_error"]],
                [e, abs(e), e * e],
                1e-8,
            )
        diffs = read("paired_differences")
        assert len(diffs) == 360
        for r in diffs.to_dict("records"):
            n, k = r["origin"], r["metric"]
            v = independently if str(r["seed"]) == "ensemble" else independent_seed
            prefix = (n,) if str(r["seed"]) == "ensemble" else None
            a = v[n, "TiDE_PHYS"] if prefix else v[n, "TiDE_PHYS", int(r["seed"])]
            b = v[n, "TiDE_DATA"] if prefix else v[n, "TiDE_DATA", int(r["seed"])]
            j = None if r["point"] == "average" else cfg["points"].index(r["point"])
            av = a[k].mean() if j is None else a[k][j]
            bv = b[k].mean() if j is None else b[k][j]
            close(
                "paired difference/" + str((n, r["seed"], r["point"], k)),
                [r["data"], r["physics"], r["difference"]],
                [bv, av, av - bv],
            )
            if bv:
                close("paired relative", r["relative_change"], av / bv - 1, 1e-8)
            else:
                assert pd.isna(r["relative_change"])
        events = [
            json.loads(line)
            for line in (root / "events.jsonl").read_text().splitlines()
        ]
        issued = [i for i, v in enumerate(events) if v["event"] == "trajectory_issued"]
        label_reads = [
            (i, v) for i, v in enumerate(events) if v["event"] == "label_prefix_read"
        ]
        assert (
            len(issued) == 4 and [events[i]["origin"] for i in issued] == cfg["origins"]
        )
        assert all(i > max(issued) for i, v in label_reads if v["rows"] == 1461)
        assert [
            v["rows"]
            for i, v in label_reads
            if v["purpose"] == "neural_fit_prefix_and_mature_previous_errors"
        ] == cfg["origins"]
        assert len([e for e in events if e["event"] == "fit_completed"]) == 24
        assert not any("teacher_fit_started" == e["event"] for e in events)
        outcome = o.read_json(root / "analysis/outcome.json")
        counts = {
            a: sum(
                p["joint_pass"]
                for p in pairs
                if p["candidate"] == a and p["reference"] == c.B
            )
            for a in cfg["arms"]
        }
        assert outcome["joint_bplus_pass_counts"] == counts
        assert outcome["replacement_goal_met"] == any(v == 3 for v in counts.values())
        assert outcome["stable_mean_policy"] == all(
            p["mean_pass"] and p["seed_agreement_pass"]
            for p in pairs
            if p["reference"] == "TiDE_DATA"
        )
        receipt.update(
            status="passed",
            checkpoints=models,
            formal_fits=24,
            optimizer_updates=9600,
            all_training_origins_verified=2 * sum(n - 432 for n in cfg["origins"]),
            full293_origin_arm_instances=full_targets,
            point_rows=len(point),
            summary_rows=len(summary),
            seed_rows=len(seed_summary),
            daily_rows=len(daily),
            paired_comparisons=len(pairs),
            event_count=len(events),
            label_issue_order_verified=True,
            new_bplus_refits=0,
            new_physical_forwards=0,
            formal_fit_failures=0,
            checks=len(checks),
            values=sum(v["values"] for v in checks),
        )
    except Exception:
        receipt.update(status="failed", error=traceback.format_exc())
        raise
    finally:
        o.write_json(out / "checks.json", checks)
        o.write_json(out / "receipt.json", receipt)
    o.write_json(
        out / "source_code.json",
        {
            str(p.relative_to(c.ROOT)): o.sha(p)
            for p in (c.ROOT / "code/tide_direct").glob("*.py")
        },
    )
    o.lock(root, "audit_lock.json", list(out.glob("*")), status="passed")
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", default="audit_v1")
    args = parser.parse_args()
    main(args.attempt)
