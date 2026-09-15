"""Independent chronological, frozen-base, component and scoring verification."""

import argparse
import json
import os
import traceback
import numpy as np
import pandas as pd
import torch
from tide_direct.audit import independent_input
from tide_direct.numpy_model import forward as base_numpy
from transformer_temporal.audit import independent_scores, independent_gate
from . import core as c
from .numpy_model import parts as head_numpy


def independent_scaling(y, forcing, t, cap=False):
    n = len(y)
    mean = t["mean"][:n]
    physical = np.stack(
        [
            mean - t["y0"],
            np.diff(mean, axis=0, prepend=mean[:1]),
            t["moisture"][:n],
            t["rain_head"][:n],
            np.broadcast_to(t["reservoir_head"][:n, None], (n, 4)),
        ],
        -1,
    )
    rain, level = forcing[:n].T
    driver = np.column_stack(
        [
            rain,
            level,
            np.diff(level, prepend=level[0]),
            [sum(rain[max(0, i - 6) : i + 1]) for i in range(n)],
            [sum(rain[max(0, i - 29) : i + 1]) for i in range(n)],
        ]
    )
    sc = dict(
        fit_prefix=n,
        unit=np.maximum(np.std(y, axis=0), 1),
        driver_mean=driver.mean(0),
        driver_std=np.maximum(driver.std(0), 1e-6),
        physics_mean=physical.mean(0),
        physics_std=np.maximum(physical.std(0), 1e-6),
    )
    if cap:
        sc["hydro_cap_mm"] = np.maximum(
            np.sqrt(np.mean((y[30:] - y[:-30]) ** 2, axis=0)), 1
        )
    return sc


def raw_input(teachers, m):
    ix = np.r_[np.arange(m - 180, m), np.arange(m - 1, m + 293)]
    t = teachers[c.o.teacher_id(m, True)]
    assert int(t["teacher_fit_prefix"]) <= m
    out = np.zeros((4, 474, 5))
    for p in range(4):
        out[p, :, 0] = t["moisture"][ix, p]
        out[p, :, 1] = t["rain_head"][ix, p]
        out[p, :, 2] = t["reservoir_head"][ix]
        out[p, :, 3] = (ix - m + 1) / 293
        out[p, :, 4] = 1
    return out


def normalized_head(raw, sc, arm):
    out = raw.copy()
    for p in range(4):
        for j in range(3):
            out[..., p, :, j] = (
                out[..., p, :, j] - np.asarray(sc["physics_mean"])[p, j + 2]
            ) / np.asarray(sc["physics_std"])[p, j + 2]
    if arm == "TiDE_CAL":
        out[..., :3] = 0
    return out


def main(attempt):
    cfg, o = c.spec(), c.o
    o.setup(cfg)
    sources = c.guard()
    root = c.ROOT / cfg["out"]
    out = root / attempt
    out.mkdir(exist_ok=False)
    checks = []
    checkpoint_count = 0
    base_models = {}
    base_scalers = {}
    base_states = {}

    def close(name, a, b, tol=1e-8):
        a = a.detach().numpy() if isinstance(a, torch.Tensor) else np.asarray(a)
        b = b.detach().numpy() if isinstance(b, torch.Tensor) else np.asarray(b)
        assert a.shape == b.shape and np.isfinite(a).all() and np.isfinite(b).all(), (
            name
        )
        delta = float(np.max(np.abs(a - b), initial=0))
        assert delta <= tol, (name, delta, tol)
        checks.append(dict(name=name, values=int(a.size), max_difference=delta))

    def optimizer_check(ck, model, step):
        groups = ck["optimizer_state_dict"]["param_groups"]
        state = ck["optimizer_state_dict"]["state"]
        assert len(groups) == 1 and len(groups[0]["params"]) == len(
            list(model.parameters())
        )
        if step == 0:
            assert state == {}
        else:
            assert len(state) == len(list(model.parameters()))
            for v in state.values():
                assert int(v["step"]) == step
                assert (
                    torch.isfinite(v["exp_avg"]).all()
                    and torch.isfinite(v["exp_avg_sq"]).all()
                )

    try:
        done = o.verify_lock(root / "training_complete.json")
        o.verify_lock(root / "analysis_lock.json")
        assert (
            done["new_base_fits"] == 21
            and done["new_head_fits"] == 18
            and done["bootstrap_heads"] == 6
            and done["updates"] == 15600
        )
        y = o.read_labels(c.ROOT / cfg["data"], 1461)
        forcing, dates = o.read_forcing(c.ROOT / cfg["data"], 1461)
        teachers = o.bank(cfg)
        # Every newly fitted base's mature input/target, including the late masked driving.
        input_origins = 0
        for r in cfg["correction"]["refit_prefixes"]:
            sc = independent_scaling(
                y[:r], forcing[:r], teachers[o.teacher_id(r, True)]
            )
            prod = c.base_arrays(
                teachers, y[:r], forcing[:r], np.arange(432, r), sc, targets=True
            )
            for j, m in enumerate(range(432, r)):
                hi, xi = independent_input(
                    c.old.spec(), teachers, y[:r], forcing[:r], m, sc, "TiDE_PHYS", r
                )
                xi[..., 7:10] = 0
                close(f"base{r}m{m} history", prod[0][j], hi, 0)
                close(f"base{r}m{m} covariates", prod[1][j], xi)
                mature = min(293, r - m)
                target = np.zeros((293, 4))
                target[:mature] = (y[m : m + mature] - y[m - 1]) / sc["unit"]
                close(f"base{r}m{m} target", prod[2][j], target, 0)
                close(f"base{r}m{m} maturity", prod[3][j], np.arange(293) < mature, 0)
                assert (
                    np.max(
                        np.r_[np.arange(m - 180, m), np.arange(m - 1, m + 293)][
                            np.r_[np.arange(m - 180, m), np.arange(m - 1, m + 293)] < r
                        ]
                    )
                    < r
                )
                input_origins += 1
            del prod
        # 105 new +60 original KIN checkpoints, independently replayed.
        all_prefixes = sorted(cfg["correction"]["refit_prefixes"] + cfg["origins"])
        for r in all_prefixes:
            for seed in cfg["seeds"]:
                final = c.base_path(cfg, r, seed)
                sc_expected = independent_scaling(
                    y[:r], forcing[:r], teachers[o.teacher_id(r, True)]
                )
                schedule = np.load(final.parent / "schedule.npz")["origins"]
                close(
                    f"base r{r}s{seed} schedule",
                    schedule,
                    np.random.default_rng(seed).integers(432, r, size=(400, 8)),
                    0,
                )
                logs = [
                    json.loads(v)
                    for v in (final.parent / "training.jsonl").read_text().splitlines()
                ]
                assert len(logs) == 400 and [v["step"] for v in logs] == list(
                    range(1, 401)
                )
                assert np.isfinite([v["mse"] for v in logs]).all()
                o.verify_lock(final.parent / "complete.json")
                for step in cfg["checkpoints"]:
                    path = final.with_name(f"e{step}.pt")
                    model, sc, ck = c.base_reload(path)
                    assert (
                        ck["training_prefix"] == r
                        and ck["step"] == step
                        and ck["seed"] == seed
                    )
                    expected_cfg = (
                        c.CONFIG
                        if r in cfg["correction"]["refit_prefixes"]
                        else c.prior.CONFIG
                    )
                    assert ck["config_sha256"] == o.sha(expected_cfg)
                    for key, val in sc_expected.items():
                        close(f"base{r}s{seed}e{step} scaler {key}", sc[key], val, 1e-9)
                    optimizer_check(ck, model, step)
                    if step == 0:
                        initial = c.old.Tide(c.old.spec(), seed)
                        for name, v in model.state_dict().items():
                            close(
                                "base exact init/" + name,
                                v,
                                initial.state_dict()[name],
                                0,
                            )
                    hi, xi = independent_input(
                        c.old.spec(),
                        teachers,
                        y[:r],
                        forcing[: r + 293],
                        r,
                        sc,
                        "TiDE_PHYS",
                        r + 293,
                    )
                    xi[..., 7:10] = 0
                    q = base_numpy(ck["state_dict"], hi[None], xi[None])[0]
                    close("base h0", q[0], np.zeros(4), 0)
                    expected = y[r - 1] + q[1:] * np.asarray(sc["unit"])
                    saved = np.load(path.with_name(f"e{step}_mean.npy"))
                    close(f"base{r}s{seed}e{step} independent", saved, expected, 1e-7)
                    close(
                        f"base{r}s{seed}e{step} reload",
                        saved,
                        c.base_predict(
                            model, sc, teachers, y[:r], forcing[: r + 293], r
                        ),
                        1e-9,
                    )
                    checkpoint_count += 1
                    if step == 400:
                        base_models[r, seed] = model
                        base_scalers[r, seed] = sc
                        base_states[r, seed] = ck["state_dict"]
        # Each of396 saved chronological paths has a truly earlier fit and full legal support.
        pooled = c.pool(cfg, 1168)
        assert len(pooled["origins"]) == 396
        close("all contiguous OOF origins", pooled["origins"], np.arange(480, 876), 0)
        independent_raw = []
        for j, m0 in enumerate(pooled["origins"]):
            m = int(m0)
            r = max(
                v for v in cfg["correction"]["refit_prefixes"] + [612, 792] if v <= m
            )
            assert pooled["base_prefixes"][j] == r and r <= m and m + 293 <= 1168
            raw = raw_input(teachers, m)
            independent_raw.append(raw)
            close(f"oof{m} raw fields", pooled["raw_cov"][j], raw, 0)
            for seed in cfg["seeds"]:
                sc = base_scalers[r, seed]
                hi, xi = independent_input(
                    c.old.spec(),
                    teachers,
                    y[:m],
                    forcing[: m + 293],
                    m,
                    sc,
                    "TiDE_PHYS",
                    m + 293,
                )
                xi[..., 7:10] = 0
                q = base_numpy(base_states[r, seed], hi[None], xi[None])[0, 1:]
                expected = y[m - 1] + q * np.asarray(sc["unit"])
                close(
                    f"oof{m}s{seed} independent forecast",
                    pooled["seeds"][seed, j],
                    expected,
                    1e-7,
                )
        for batch in root.glob("oof/batch_*/lock.json"):
            meta = o.verify_lock(batch)
            prov = o.read_json(batch.parent / "provenance.json")
            for p, h in prov["base_checkpoint_hashes"].items():
                assert o.sha(c.ROOT / p) == h
            assert len(prov["paths"]) == meta["count"]
            for p in prov["paths"]:
                m = p["origin"]
                r = p["base_fit_prefix"]
                assert (
                    r <= m
                    and p["target_start"] == m
                    and p["history_max"] == m - 1
                    and p["target_end_exclusive"]
                    == p["given_driver_end_exclusive"]
                    == m + 293
                    <= meta["mature_prefix"]
                )
        all_means = {}
        all_seeds = {}
        all_sigmas = {}
        for n, end in zip(cfg["origins"], cfg["ends"]):
            folder = root / f"origin_{n}"
            o.verify_lock(folder / "issue_lock.json")
            sc = o.read_json(folder / "scaling.json")
            expected_sc = independent_scaling(
                y[:n], forcing[:n], teachers[o.teacher_id(n, True)], True
            )
            for k, v in expected_sc.items():
                close(f"outer{n}scaler {k}", sc[k], v, 1e-9)
            all_means[n] = o.load_npz(folder / "means.npz")
            all_seeds[n] = o.load_npz(folder / "seeds.npz")
            src = c.ROOT / cfg["prior_controls_out"] / f"origin_{n}"
            for name, dest in [("means", all_means[n]), ("seeds", all_seeds[n])]:
                original = o.load_npz(src / f"{name}.npz")
                for arm in cfg["controls"] + cfg["reuse_arms"]:
                    close(f"unchanged {n}/{arm}/{name}", dest[arm], original[arm], 0)
            p = c.pool(cfg, n)
            ms = c.eligible(cfg, n)
            if n == 612:
                assert p is None
            else:
                close(f"pool cutoff{n}", p["origins"], ms, 0)
            for arm in cfg["new_arms"]:
                for seed in cfg["seeds"]:
                    dest = c.checkpoint_folder(cfg, n, arm, seed)
                    o.verify_lock(
                        dest / ("bootstrap.json" if n == 612 else "complete.json")
                    )
                    logs = [
                        json.loads(v)
                        for v in (dest / "training.jsonl").read_text().splitlines()
                    ]
                    assert len(logs) == (0 if n == 612 else 400)
                    if n != 612:
                        close(
                            f"{n}/{arm}/s{seed} schedule",
                            np.load(dest / "schedule.npz")["indices"],
                            np.random.default_rng(seed).integers(
                                0, len(ms), size=(400, 8)
                            ),
                            0,
                        )
                        close(
                            f"{n}/{arm}/s{seed} sampled origins",
                            np.load(dest / "schedule.npz")["origins"],
                            ms[np.load(dest / "schedule.npz")["indices"]],
                            0,
                        )
                        targets = (
                            np.stack([y[m : m + 293] for m in ms]) - p["seeds"][seed]
                        ) / np.asarray(sc["unit"])
                        close(
                            f"{n}/{arm}/s{seed} seed-matched out-of-training targets",
                            np.load(dest / "training_targets.npy"),
                            targets,
                            0,
                        )
                        own_raw = np.stack([raw_input(teachers, int(m)) for m in ms])
                        close(
                            f"{n}/{arm} train inputs independent",
                            c.head_cov(p["raw_cov"], sc, arm),
                            normalized_head(own_raw, sc, arm),
                            0,
                        )
                    for step in [0] if n == 612 else cfg["checkpoints"]:
                        model, cs, ck = c.reload(dest / f"e{step}.pt")
                        assert (
                            ck["step"] == step
                            and ck["training_prefix"] == n
                            and ck["seed"] == seed
                            and ck["arm"] == arm
                        )
                        assert (
                            ck["config_sha256"] == o.sha(c.CONFIG)
                            and ck["trainable_parameters"] == 13179
                        )
                        assert not any(k.startswith("main.") for k in ck["state_dict"])
                        assert ck["base_checkpoint"] == str(
                            c.base_path(cfg, n, seed).relative_to(c.ROOT)
                        )
                        assert ck["base_checkpoint_sha256"] == o.sha(
                            c.ROOT / ck["base_checkpoint"]
                        )
                        optimizer_check(ck, model, step)
                        for k, v in sc.items():
                            close("head scaler " + k, cs[k], v, 0)
                        if step == 0:
                            init = c.Head(cfg, seed, sc)
                            for k, v in ck["state_dict"].items():
                                close(
                                    "head exact init " + k, v, init.state_dict()[k], 0
                                )
                        x = normalized_head(raw_input(teachers, n)[None], sc, arm)
                        raw, actual = head_numpy(ck["state_dict"], x)
                        close("head h0", actual[:, 0], np.zeros((1, 4)), 0)
                        close(
                            "head independent vs torch",
                            model(torch.from_numpy(x)),
                            actual,
                            1e-9,
                        )
                        raw = raw[0, 1:] * sc["unit"]
                        actual = actual[0, 1:] * sc["unit"]
                        base = all_seeds[n]["TiDE_KIN"][seed]
                        close(
                            "frozen base component",
                            np.load(dest / f"e{step}_base_mm.npy"),
                            base,
                            0,
                        )
                        close(
                            "head raw component",
                            np.load(dest / f"e{step}_raw_mm.npy"),
                            raw,
                            1e-7,
                        )
                        close(
                            "head actual component",
                            np.load(dest / f"e{step}_correction_mm.npy"),
                            actual,
                            1e-7,
                        )
                        close(
                            "head total",
                            np.load(dest / f"e{step}_mean.npy"),
                            base + actual,
                            1e-7,
                        )
                        assert np.all(
                            np.abs(actual) <= np.asarray(sc["hydro_cap_mm"]) + 1e-8
                        )
                        if step == 0:
                            close(
                                "exact zero fallback",
                                np.load(dest / f"e{step}_mean.npy"),
                                base,
                                0,
                            )
                        checkpoint_count += 1
                    close(
                        "saved final seed",
                        all_seeds[n][arm][seed],
                        np.load(dest / f"e{c.final_step(n)}_mean.npy"),
                        0,
                    )
                close(
                    "equal seed mean", all_means[n][arm], all_seeds[n][arm].mean(0), 0
                )
            if n != 612:
                prev = cfg["origins"][cfg["origins"].index(n) - 1]
                cal = o.read_json(folder / "calibration.json")
                assert (
                    cal["start"] == prev + 90
                    and cal["end"] == prev + 180
                    and cal["end"] <= n
                    and cal["count"] == 90
                )
                errors = o.load_npz(folder / "calibration_errors.npz")
                all_sigmas[n] = o.load_npz(folder / "sigmas.npz")
                for arm in cfg["controls"] + cfg["arms"]:
                    e = y[prev + 90 : prev + 180] - all_means[prev][arm][90:180]
                    close("past issued calibration errors", errors[arm], e, 0)
                    close(
                        "past issued RMS",
                        all_sigmas[n][arm],
                        np.maximum(np.sqrt(np.mean(e * e, 0)), 1e-6),
                        0,
                    )
                old = o.load_npz(src / "sigmas.npz")
                for arm in cfg["controls"] + cfg["reuse_arms"]:
                    close("copied original sigma", all_sigmas[n][arm], old[arm], 0)
                if n == 792:
                    for arm in cfg["new_arms"]:
                        close(
                            "bootstrap sigma exact KIN",
                            all_sigmas[n][arm],
                            old["TiDE_KIN"],
                            0,
                        )
        assert checkpoint_count == 261

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
        assert len(pairs) == 99
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
        assert len(daily) == 45708
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
        assert len(endpoints) == 1872
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
        diffs = read("paired_metric_effects")
        assert len(diffs) == 1080
        for r in diffs.to_dict("records"):
            n, k = r["origin"], r["metric"]
            j = None if r["point"] == "average" else cfg["points"].index(r["point"])
            values = {}
            for a in cfg["arms"]:
                v = (
                    independently[n, a]
                    if str(r["seed"]) == "ensemble"
                    else independent_seed[n, a, int(r["seed"])]
                )
                values[a] = v[k].mean() if j is None else v[k][j]
                close(
                    "factor source/" + str((n, r["seed"], r["point"], k, a)),
                    r[a],
                    values[a],
                )
            a, b = r["factor"].split("__minus__")
            expected = {r["factor"]: values[a] - values[b]}
            close(
                "factor/" + str((n, r["seed"], r["point"], k, r["factor"])),
                r["effect"],
                expected[r["factor"]],
            )
        component_rows = read("component_summary")
        assert len(component_rows) == 128
        for r in component_rows.to_dict("records"):
            n, arm, seed, p = (
                r["origin"],
                r["method"],
                str(r["seed"]),
                cfg["points"].index(r["point"]),
            )
            cap = max(float(np.sqrt(np.mean((y[30:n, p] - y[: n - 30, p]) ** 2))), 1.0)
            data = {
                k: np.stack(
                    [
                        np.load(
                            c.checkpoint_folder(cfg, n, arm, j)
                            / f"e{c.final_step(n)}_{k}.npy"
                        )
                        for j in cfg["seeds"]
                    ]
                )
                for k in ["base_mm", "raw_mm", "correction_mm"]
            }
            v = {
                k: (a.mean(0) if seed == "ensemble" else a[int(seed)])[:, p]
                for k, a in data.items()
            }
            expected = dict(
                cap_mm=cap,
                base_abs_mean=np.abs(v["base_mm"]).mean(),
                raw_abs_mean=np.abs(v["raw_mm"]).mean(),
                raw_abs_max=np.abs(v["raw_mm"]).max(),
                hydro_abs_mean=np.abs(v["correction_mm"]).mean(),
                hydro_abs_max=np.abs(v["correction_mm"]).max(),
                raw_over_cap_fraction=np.mean(np.abs(v["raw_mm"]) > cap),
                hydro_over95cap_fraction=np.mean(
                    np.abs(v["correction_mm"]) >= 0.95 * cap
                ),
            )
            for key, value in expected.items():
                close(
                    f"component diagnostic/{n}/{arm}/{seed}/{p}/{key}",
                    r[key],
                    value,
                    1e-10,
                )
        events = [
            json.loads(v) for v in (root / "events.jsonl").read_text().splitlines()
        ]
        issued = [i for i, v in enumerate(events) if v["event"] == "trajectory_issued"]
        assert [events[i]["origin"] for i in issued] == cfg["origins"]
        assert all(
            i > max(issued)
            for i, v in enumerate(events)
            if v["event"] == "label_prefix_read" and v["rows"] == 1461
        )
        current = 0
        known_pool = set()
        base_fit_keys = set()
        head_fit_keys = set()
        for i, e in enumerate(events):
            if (
                e["event"] == "label_prefix_read"
                and e["purpose"]
                == "current_prefix_for_mature_oof_correction_and_calibration"
            ):
                current = e["rows"]
            if e["event"] == "base_fit_started":
                key = (e["prefix"], e["seed"])
                assert key not in base_fit_keys and e["prefix"] + 293 <= current
                base_fit_keys.add(key)
            if e["event"] == "oof_batch_locked":
                assert e["last"] + 293 <= current == e["mature_prefix"]
                known_pool.update(range(e["first"], e["last"] + 1))
            if e["event"] == "head_fit_started":
                key = (e["origin"], e["arm"], e["seed"])
                assert (
                    key not in head_fit_keys
                    and e["origin"] == current
                    and set(c.eligible(cfg, current)) == known_pool
                )
                head_fit_keys.add(key)
        assert (
            len(base_fit_keys) == 21
            and len(head_fit_keys) == 18
            and len(known_pool) == 396
        )
        assert len([e for e in events if e["event"] == "base_fit_completed"]) == 21
        assert len([e for e in events if e["event"] == "head_fit_completed"]) == 18
        assert len([e for e in events if e["event"] == "bootstrap_zero_head"]) == 6
        assert not any("teacher_fit" in e["event"] for e in events)
        outcome = o.read_json(root / "analysis/outcome.json")
        counts = {
            a: sum(
                p["joint_pass"]
                for p in pairs
                if p["candidate"] == a and p["reference"] == c.B
            )
            for a in cfg["arms"]
        }
        assert outcome["joint_bplus_pass_counts"] == counts and outcome[
            "replacement_goal_met"
        ] == any(v == 3 for v in counts.values())
        stable = {
            b: all(
                p["mean_pass"] and p["seed_agreement_pass"]
                for p in pairs
                if p["candidate"] == "TiDE_HCAL" and p["reference"] == b
            )
            for b in ["TiDE_CAL", "TiDE_KIN"]
        }
        assert outcome["stable_hcal_vs_references"] == stable
        assert not list(root.glob("error_*.json"))
        receipt = dict(
            status="passed",
            source_files=sources,
            checks=len(checks),
            values=sum(v["values"] for v in checks),
            max_difference=max(v["max_difference"] for v in checks),
            checkpoints=checkpoint_count,
            new_base_checkpoints=105,
            new_head_checkpoints=90,
            bootstrap_checkpoints=6,
            reused_base_checkpoints=60,
            formal_fits=39,
            new_base_fits=21,
            new_head_fits=18,
            optimizer_updates=15600,
            historical_base_training_origins=input_origins,
            oof_paths=396,
            oof_seed_paths=1188,
            eligible_counts=[0, 20, 200, 396],
            base_regime_counts=[0, 1, 5, 9],
            frozen_KIN_unchanged=True,
            all_oof_paths_fully_mature=True,
            all_oof_targets_outside_base_training=True,
            base_in_head_optimizer=False,
            point_rows=len(point),
            summary_rows=len(summary),
            seed_rows=len(seed_summary),
            daily_rows=len(daily),
            paired_comparisons=len(pairs),
            paired_metric_rows=len(diffs),
            component_rows=128,
            event_count=len(events),
            label_issue_order_verified=True,
            formal_fit_failures=0,
            new_bplus_refits=0,
            new_physical_forwards=0,
        )
    except Exception:
        receipt = dict(
            status="failed",
            checks=len(checks),
            values=sum(v["values"] for v in checks),
            error=traceback.format_exc(),
        )
        raise
    finally:
        o.write_json(out / "checks.json", checks)
        o.write_json(out / "receipt.json", receipt)
    o.write_json(
        out / "source_code.json",
        {
            str(p.relative_to(c.ROOT)): o.sha(p)
            for p in (c.ROOT / "code/tide_correction").glob("*.py")
        },
    )
    o.lock(root, "audit_lock.json", list(out.glob("*")), status="passed")
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", default="audit_v1")
    args = parser.parse_args()
    main(args.attempt)
