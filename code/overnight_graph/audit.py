"""Independent NumPy GRU, graph, chronology, decomposition and scoring audit."""

import argparse
import json
import traceback

import numpy as np
import pandas as pd
from scipy.special import expit, ndtr
from scipy.signal import lfilter

from transformer_temporal.audit import independent_gate, independent_scores
from .core import (
    B,
    CONFIG,
    ROOT,
    bank,
    guard,
    inputs,
    load_npz,
    predict,
    read_forcing,
    read_json,
    read_labels,
    reload,
    setup,
    sha,
    spec,
    target_bank,
    targets,
    teacher_id,
    utc,
    verify_implementation,
    verify_lock,
    write_json,
)


def physical(t):
    forcing = t["forcing"]
    cumulative = np.concatenate([[0.0], np.cumsum(forcing[:, 0])])
    ends = np.arange(1, len(forcing) + 1)
    rain7 = cumulative[ends] - cumulative[np.maximum(0, ends - 7)]
    rain30 = cumulative[ends] - cumulative[np.maximum(0, ends - 30)]
    dr = np.concatenate([[0.0], np.diff(forcing[:, 1])])
    result = []
    for j in range(4):
        result.append(
            np.column_stack(
                [
                    t["mean"][:, j] - t["y0"][j],
                    np.concatenate([[0.0], np.diff(t["mean"][:, j])]),
                    forcing[:, 0],
                    forcing[:, 1],
                    dr,
                    rain7,
                    rain30,
                    t["moisture"][:, j],
                    t["rain_head"][:, j],
                    t["reservoir_head"],
                ]
            )
        )
    return np.stack(result, axis=1)


def raw(t, y):
    result = physical(t)[: len(y)]
    observed = y - y[0]
    dy = np.concatenate([np.zeros((1, 4)), y[1:] - y[:-1]])
    residual = y - t["mean"][: len(y)]
    dr = np.concatenate([np.zeros((1, 4)), residual[1:] - residual[:-1]])
    return np.concatenate(
        [result, np.stack([observed, dy, residual, dr], axis=-1)], axis=-1
    )


def independent_inputs(teachers, y, ms, hs, sc, current=False):
    ms, hs = np.asarray(ms), np.asarray(hs)
    hist = np.zeros((len(ms), 4, int(max(ms)), 14))
    future = np.zeros((*hs.shape, 4, 10))
    av, sd = np.array(sc["mean"]), np.array(sc["std"])
    for i, m in enumerate(ms):
        tid = (
            1168
            if current and m == 1168
            else 792
            if m >= 792
            else 612
            if m >= 612
            else 432
        )
        teacher = teachers[tid]
        hist[i, :, :m] = ((raw(teacher, y[:m]) - av) / sd).transpose(1, 0, 2)
        future[i] = (physical(teacher)[m + hs[i] - 1] - av[:, :10]) / sd[:, :10]
    distance = np.stack([hs / 293, np.log1p(hs) / np.log(294)], axis=-1)
    return hist, ms, future, distance


def numpy_forward(saved, tensors, baseline):
    w = {k: v.detach().numpy() for k, v in saved["state_dict"].items()}
    hist, lengths, future, distance = tensors
    assert len(lengths) == 1

    def linear(x, name):
        return (
            np.einsum("...i,oi->...o", x, w[name + ".weight"], optimize=False)
            + w[name + ".bias"]
        )

    h = np.zeros((4, 8))
    x = hist[0, :, : int(lengths[0])]
    for time in range(x.shape[1]):
        gi = (
            np.einsum("pi,oi->po", x[:, time], w["gru.weight_ih_l0"], optimize=False)
            + w["gru.bias_ih_l0"]
        )
        gh = (
            np.einsum("pi,oi->po", h, w["gru.weight_hh_l0"], optimize=False)
            + w["gru.bias_hh_l0"]
        )
        reset = expit(gi[:, :8] + gh[:, :8])
        update = expit(gi[:, 8:16] + gh[:, 8:16])
        new = np.tanh(gi[:, 16:] + reset * gh[:, 16:])
        h = (1 - update) * new + update * h
    context = np.tanh(
        linear(np.einsum("pq,qk->pk", w["adjacency"], h, optimize=False), "message")
    )
    q = future.shape[1]
    last = x[:, -1, [11, 12]]
    z = np.concatenate(
        [
            np.broadcast_to(h, (q, 4, 8)),
            np.broadcast_to(context, (q, 4, 8)),
            future[0],
            np.broadcast_to(last, (q, 4, 2)),
            np.broadcast_to(distance[0, :, None], (q, 4, 2)),
            np.broadcast_to(w["point.weight"], (q, 4, 4)),
        ],
        axis=-1,
    )
    z = linear(z, "decode")
    components = (
        linear(z * ndtr(z), "head") * np.array(saved["scaling"]["unit"])[None, :, None]
    )
    return baseline + components.sum(-1), components


def causal_filter(r):
    alpha = 2 / 31
    return lfilter(
        [alpha], [1, -(1 - alpha)], r, axis=0, zi=((1 - alpha) * r[0])[None]
    )[0]


def main(attempt):
    cfg = spec()
    setup(cfg)
    root = ROOT / cfg["out"]
    out = root / attempt
    out.mkdir(exist_ok=False)
    receipt = dict(
        status="running",
        started_utc=utc(),
        values_checked=0,
        max_difference=0.0,
        checkpoints=0,
        fits=0,
        updates=0,
        new_fits=0,
        physical_forwards=0,
    )

    def close(a, b, tol=1e-8):
        a, b = np.asarray(a, float), np.asarray(b, float)
        assert a.shape == b.shape, (a.shape, b.shape)
        assert np.isfinite(a).all() and np.isfinite(b).all()
        difference = float(np.max(abs(a - b))) if a.size else 0.0
        assert difference <= tol, (difference, tol)
        receipt["values_checked"] += int(a.size)
        receipt["max_difference"] = max(receipt["max_difference"], difference)

    try:
        verify_implementation(cfg)
        receipt["source_files"] = guard()
        verify_lock(root / "analysis_lock.json")
        verify_lock(root / "analysis_base_lock.json")
        verify_lock(root / "diagnostic/lock.json")
        decision = read_json(root / "diagnostic/decision.json")
        phases = ["base"] + (["dual"] if decision["triggered"] else [])
        y = read_labels(ROOT / cfg["data"], 1461)
        forcing, dates = read_forcing(ROOT / cfg["data"], 1461)
        teachers = bank(cfg)
        summary = (
            pd.read_csv(root / "analysis/phase_summary.csv")
            .set_index(["origin", "method"])
            .sort_index()
        )
        points = (
            pd.read_csv(root / "analysis/metrics_by_point.csv")
            .set_index(["origin", "method", "point"])
            .sort_index()
        )
        seedtable = (
            pd.read_csv(root / "analysis/seed_summary.csv")
            .set_index(["origin", "method", "seed"])
            .sort_index()
        )
        daily = pd.read_csv(root / "analysis/daily_predictions.csv")
        gates = read_json(root / "analysis/effect_gates.json")
        pairings = read_json(root / "analysis/pairing.json")
        issued_means = {}
        issued_seeds = {}
        issued_sigmas = {}
        for phase in phases:
            verify_lock(root / phase / "training_complete.json")
            arms = cfg["arms"] if phase == "base" else [cfg["conditional_arm"]]
            previous = None
            for n, end in zip(cfg["origins"], cfg["ends"]):
                folder = root / phase / f"origin_{n}"
                verify_lock(folder / "issue_lock.json")
                t = teachers[teacher_id(n, True)]
                np.testing.assert_array_equal(t["dates"][:end], dates[:end])
                close(t["forcing"][:end], forcing[:end], 0)
                sc = read_json(folder / "scaling.json")
                ra = raw(t, y[:n])
                close(sc["mean"], ra.mean(0), 1e-12)
                close(sc["std"], np.maximum(ra.std(0), 1e-6), 1e-12)
                close(sc["unit"], np.maximum(y[:n].std(0), 1), 0)
                close(sc["y0"], y[0], 0)
                assert sc["fit_prefix"] == n
                ins = independent_inputs(
                    teachers, y[:n], [n], np.arange(1, 294)[None], sc, True
                )
                for a, b in zip(
                    ins, inputs(teachers, y[:n], [n], np.arange(1, 294)[None], sc, True)
                ):
                    close(a, b.numpy(), 1e-9)
                means = load_npz(folder / "means.npz")
                seeds = load_npz(folder / "seeds.npz")
                for arm in arms:
                    stack = []
                    for seed in cfg["seeds"]:
                        dest = folder / arm / f"seed_{seed}"
                        done = verify_lock(dest / "complete.json")
                        assert done["new_fits"] == 1 and done["updates"] == 200
                        receipt["fits"] += 1
                        receipt["updates"] += 200
                        schedule = load_npz(dest / "schedule.npz")
                        ms, hs = schedule["origins"], schedule["horizons"]
                        rng = np.random.default_rng(seed)
                        expected_ms = rng.integers(432, n, size=(200, 4))
                        expected_hs = np.array(
                            [
                                [rng.integers(1, min(293, n - m) + 1, 16) for m in row]
                                for row in expected_ms
                            ]
                        )
                        close(ms, expected_ms, 0)
                        close(hs, expected_hs, 0)
                        assert np.all(ms[:, :, None] + hs - 1 < n)
                        cache = target_bank(teachers, y[:n])
                        for tid, c in cache.items():
                            r = y[: len(c)] - teachers[tid]["mean"][: len(c)]
                            slow = causal_filter(r)
                            close(c, np.stack([slow, r - slow], axis=-1), 1e-9)
                        for index in [0, 199]:
                            independent = independent_inputs(
                                teachers, y[:n], ms[index], hs[index], sc
                            )
                            actual = inputs(teachers, y[:n], ms[index], hs[index], sc)
                            for a, b in zip(independent, actual):
                                close(a, b.numpy(), 1e-9)
                            expected = []
                            for m, hh in zip(ms[index], hs[index]):
                                tid = teacher_id(m)
                                ix = m + hh - 1
                                assert tid <= m and max(ix) < n
                                r = (
                                    y[: min(n, len(teachers[tid]["mean"]))]
                                    - teachers[tid]["mean"][:n]
                                )
                                slow = causal_filter(r)
                                v = (
                                    np.stack([slow[ix], r[ix] - slow[ix]], axis=-1)
                                    / np.array(sc["unit"])[None, :, None]
                                )
                                expected.append(
                                    v if phase == "dual" else v.sum(-1, keepdims=True)
                                )
                            close(
                                targets(
                                    cache, ms[index], hs[index], sc, phase == "dual"
                                ).numpy(),
                                expected,
                                1e-9,
                            )
                        trace = [
                            json.loads(line)
                            for line in (dest / "training.jsonl")
                            .read_text()
                            .splitlines()
                        ]
                        assert [r["step"] for r in trace] == list(range(1, 201))
                        close(
                            [r["loss"] for r in trace],
                            [
                                r["mse"] + r["penalty"] + 0.5 * r["auxiliary"]
                                for r in trace
                            ],
                            1e-12,
                        )
                        assert all(np.isfinite(r["grad_norm"]) for r in trace)
                        if phase == "base":
                            assert all(r["auxiliary"] == 0 for r in trace)
                        for step in cfg["checkpoints"]:
                            model, scale, saved = reload(dest / f"e{step}.pt", cfg)
                            assert scale == sc and (
                                saved["seed"],
                                saved["arm"],
                                saved["step"],
                                saved["training_prefix"],
                                saved["phase"],
                            ) == (seed, arm, step, n, phase)
                            assert saved["config_sha256"] == sha(CONFIG)
                            expected = np.load(dest / f"e{step}_mean.npy")
                            ec = np.load(dest / f"e{step}_components.npy")
                            a, c = predict(model, teachers, y[:n], n, end, sc)
                            close(a, expected, 0)
                            close(c, ec, 0)
                            a, c = numpy_forward(saved, ins, t["mean"][n:end])
                            close(a, expected, 1e-7)
                            close(c, ec, 1e-7)
                            if step == 0:
                                close(expected, t["mean"][n:end], 0)
                                close(ec, np.zeros_like(ec), 0)
                            adjacency = np.eye(4)
                            if arm != "GRU_LOCAL":
                                adjacency[2, 3] = adjacency[3, 2] = 1
                                adjacency /= adjacency.sum(1)[:, None]
                            close(
                                saved["state_dict"]["adjacency"].numpy(), adjacency, 0
                            )
                            receipt["checkpoints"] += 1
                        stack.append(np.load(dest / "e200_mean.npy"))
                    close(seeds[arm], np.stack(stack), 0)
                    close(means[arm], np.mean(stack, 0), 0)
                if phase == "base":
                    old = ROOT / cfg["prior_out"] / f"origin_{n}"
                    for method in cfg["controls"]:
                        close(means[method], load_npz(old / "means.npz")[method], 0)
                        close(seeds[method], load_npz(old / "seeds.npz")[method], 0)
                    close(means[B], t["mean"][n:end], 0)
                    close(
                        means["DRIFT1"],
                        y[n - 1] + np.arange(1, 294)[:, None] * (y[n - 1] - y[n - 2]),
                        0,
                    )
                issued_means.setdefault(n, {}).update(means)
                issued_seeds.setdefault(n, {}).update(seeds)
                if previous is not None:
                    sigma = load_npz(folder / "sigmas.npz")
                    errors = load_npz(folder / "calibration_errors.npz")
                    prior = load_npz(root / phase / f"origin_{previous}/means.npz")
                    assert read_json(folder / "calibration.json") == dict(
                        previous_origin=previous,
                        start=previous + 90,
                        end=previous + 180,
                        matured_before=n,
                        count=90,
                    )
                    assert previous + 180 <= n
                    for method in means:
                        err = y[previous + 90 : previous + 180] - prior[method][90:180]
                        close(errors[method], err, 0)
                        close(
                            sigma[method],
                            np.maximum(np.sqrt((err * err).mean(0)), 1e-6),
                            0,
                        )
                        if method in cfg["controls"]:
                            close(
                                sigma[method],
                                load_npz(
                                    ROOT / cfg["prior_out"] / f"origin_{n}/sigmas.npz"
                                )[method],
                                0,
                            )
                    issued_sigmas.setdefault(n, {}).update(sigma)
                previous = n
        for n, end in list(zip(cfg["origins"], cfg["ends"]))[1:]:
            metrics, seed_metrics = {}, {}
            for method, mean in issued_means[n].items():
                sigma = issued_sigmas[n][method]
                metrics[method] = independent_scores(y[n:end], mean, sigma)
                for key, values in metrics[method].items():
                    close(
                        points.loc[(n, method), key].reindex(cfg["points"]).values,
                        values,
                    )
                    close(summary.loc[(n, method), key], values.mean())
                assert summary.loc[(n, method), "n_per_point"] == 293
                seed_metrics[method] = []
                for seed, sm in enumerate(issued_seeds[n][method]):
                    mt = independent_scores(y[n:end], sm, sigma)
                    seed_metrics[method].append(mt)
                    for key, values in mt.items():
                        close(seedtable.loc[(n, method, seed), key], values.mean())
                part = daily[(daily.origin == n) & (daily.method == method)]
                assert len(part) == 1172 and (part.issue_date == dates[n]).all()
                for j, p in enumerate(cfg["points"]):
                    q = part[part.point == p].sort_values("target_index")
                    np.testing.assert_array_equal(q.date.values, dates[n:end])
                    close(q.target_index, np.arange(n, end), 0)
                    close(q.distance, np.arange(1, 294), 0)
                    close(q.observed, y[n:end, j])
                    close(q["mean"], mean[:, j])
                    close(q.sigma, np.full(293, sigma[j]))
            for method, values in metrics.items():
                assert (
                    independent_gate(values, metrics[B], cfg) == gates[str(n)][method]
                )
            for r in [r for r in pairings if r["origin"] == n]:
                a, b = r["candidate"], r["reference"]
                for key, value in independent_gate(metrics[a], metrics[b], cfg).items():
                    assert r[key] == value
                flags = [
                    all(
                        seed_metrics[a][s][k].mean() < seed_metrics[b][s][k].mean()
                        for k in ["mae", "rmse"]
                    )
                    for s in cfg["seeds"]
                ]
                assert r["seed_flags"] == flags and r["seed_both_improve"] == sum(flags)
        # Independently recompute every causal residual component and every trigger input.
        dc = cfg["conditional_diagnostic"]
        component = pd.read_csv(root / "diagnostic/components.csv")
        var = pd.read_csv(root / "diagnostic/variances.csv")
        corr = pd.read_csv(root / "diagnostic/correlations.csv")
        csum = np.r_[0, np.cumsum(forcing[:1152, 0])]
        idx = np.arange(1, 1153)
        drivers = {
            "rain7": csum[idx] - csum[np.maximum(0, idx - 7)],
            "rwl_change": np.r_[0, np.diff(forcing[:1152, 1])],
        }
        independently_eligible = []
        for start, end, tid in dc["blocks"]:
            r = y[start:end] - teachers[tid]["mean"][start:end]
            slow = causal_filter(r)
            fast = r - slow
            for j, p in enumerate(cfg["points"]):
                part = component[
                    (component.start == start) & (component.point == p)
                ].sort_values("index")
                close(part.residual, r[:, j])
                close(part.slow, slow[:, j])
                close(part.fast, fast[:, j])
                close(part.diagnostic_retained, np.arange(180) >= 30, 0)
                v = var[(var.start == start) & (var.point == p)].iloc[0]
                rv = np.var(r[30:, j])
                sv = np.var(slow[30:, j])
                fv = np.var(fast[30:, j])
                cv = np.cov(slow[30:, j], fast[30:, j], ddof=0)[0, 1]
                close(
                    [
                        v.total_variance,
                        v.slow_variance,
                        v.fast_variance,
                        v.covariance,
                        v.fast_ratio,
                    ],
                    [rv, sv, fv, cv, fv / rv],
                )
                close(rv, sv + fv + 2 * cv)
                ix = np.arange(start + 30, end)
                for driver in dc["drivers"]:
                    for lag in dc["lags"]:
                        a = drivers[driver][ix - lag]
                        b = fast[30:, j]
                        a = a - a.mean()
                        b = b - b.mean()
                        rho = np.sum(a * b) / np.sqrt(np.sum(a * a) * np.sum(b * b))
                        row = corr[
                            (corr.start == start)
                            & (corr.point == p)
                            & (corr.driver == driver)
                            & (corr.lag == lag)
                        ].iloc[0]
                        close(row.correlation, rho)
                        close(row.fast_ratio, fv / rv)
                        independently_eligible.append(
                            dict(
                                start=start,
                                point=p,
                                driver=driver,
                                lag=lag,
                                rho=rho,
                                ratio=fv / rv,
                            )
                        )
        groups = []
        for driver in dc["drivers"]:
            for lag in dc["lags"]:
                for sign in [-1, 1]:
                    passed = []
                    for p in cfg["points"]:
                        blocks = [
                            r["start"]
                            for r in independently_eligible
                            if (r["point"], r["driver"], r["lag"]) == (p, driver, lag)
                            and sign * r["rho"] >= 0.3
                            and r["ratio"] >= 0.1
                        ]
                        saved = next(
                            r
                            for r in decision["eligible"]
                            if (r["point"], r["driver"], r["lag"], r["sign"])
                            == (p, driver, lag, sign)
                        )
                        assert saved["passing_blocks"] == blocks and saved[
                            "passed"
                        ] == (len(blocks) >= 3)
                        if len(blocks) >= 3:
                            passed.append(p)
                    groups.append(
                        dict(
                            driver=driver,
                            lag=lag,
                            sign=sign,
                            points=passed,
                            passed=len(passed) >= 2,
                        )
                    )
        assert decision["groups"] == groups and decision["triggered"] == any(
            r["passed"] for r in groups
        )
        events = [
            json.loads(line)
            for line in (root / "events.jsonl").read_text().splitlines()
        ]
        for phase in phases:
            start = [
                r for r in events if r["event"] == "fit_started" and r["phase"] == phase
            ]
            end = [
                r
                for r in events
                if r["event"] == "fit_completed" and r["phase"] == phase
            ]
            issue = [
                r
                for r in events
                if r["event"] == "trajectory_issued" and r["phase"] == phase
            ]
            assert (
                len(start) == len(end) == (24 if phase == "base" else 12)
                and len(issue) == 4
            )
            scoring = next(
                i
                for i, r in enumerate(events)
                if r["event"] == "label_prefix_read"
                and r["purpose"]
                == ("analysis_base" if phase == "base" else "analysis")
                + "_all_issued_before_full_scoring"
            )
            assert all(events.index(r) < scoring for r in issue)
            for n in cfg["origins"]:
                read = next(
                    r
                    for r in events
                    if r["event"] == "label_prefix_read"
                    and r["rows"] == n
                    and r["purpose"].startswith(phase + "_fit")
                )
                issued = next(r for r in issue if r["origin"] == n)
                assert read["time_utc"] < issued["time_utc"]
                assert all(
                    read["time_utc"] < r["time_utc"] < issued["time_utc"]
                    for r in end
                    if r["origin"] == n
                )
        number = len(issued_means[1168])
        assert (len(summary), len(points), len(seedtable), len(daily)) == (
            number * 3,
            number * 12,
            number * 9,
            number * 3 * 293 * 4,
        )
        receipt.update(
            status="passed",
            finished_utc=utc(),
            phases=phases,
            events_checked=len(events),
            diagnostic_triggered=decision["triggered"],
            summary_rows=len(summary),
            point_rows=len(points),
            seed_rows=len(seedtable),
            daily_rows=len(daily),
            script_sha256=sha(ROOT / "code/overnight_graph/audit.py"),
        )
    except Exception:
        receipt.update(
            status="failed", finished_utc=utc(), error=traceback.format_exc()
        )
        write_json(out / "receipt.json", receipt)
        raise
    write_json(out / "receipt.json", receipt)
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", default="independent_audit")
    main(parser.parse_args().attempt)
