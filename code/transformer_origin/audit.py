"""Read-only independent NumPy forward, chronology and saved-result audit."""

import argparse
import json
import math
import traceback

import numpy as np
import pandas as pd
from scipy.special import ndtr

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
    targets,
    utc,
    verify_implementation,
    verify_lock,
    write_json,
)


def teacher_id(m, current=False):
    if current and m == 1168:
        return 1168
    return 792 if m >= 792 else 612 if m >= 612 else 432


def independent_inputs(teachers, y, ms, hs, sc, current=False):
    ms, hs = np.asarray(ms), np.asarray(hs)
    length = int(max(ms))
    history = np.zeros((len(ms), length, 30))
    future = np.zeros((*hs.shape, 22))
    valid = np.arange(length)[None] < ms[:, None]
    for i, m in enumerate(ms):
        t = teachers[teacher_id(m, current)]
        raw = np.concatenate(
            (t["x"][:m], y[:m] - np.array(sc["y0"]), y[:m] - t["mean"][:m]), axis=1
        )
        history[i, :m] = (raw - sc["mean"]) / sc["std"]
        future[i] = (t["x"][m + hs[i] - 1] - np.array(sc["mean"])[:22]) / np.array(
            sc["std"]
        )[:22]
    return history, future, valid, np.arange(length)[None] - ms[:, None], hs - 1


def numpy_forward(saved, tensors, baseline):
    w = {k: v.detach().numpy() for k, v in saved["state_dict"].items()}

    def linear(x, name):
        return (
            np.einsum("...i,oi->...o", x, w[name + ".weight"], optimize=False)
            + w[name + ".bias"]
        )

    def ln(x, name):
        z = x - x.mean(-1, keepdims=True)
        return (
            z
            / np.sqrt(np.mean(z * z, axis=-1, keepdims=True) + 1e-5)
            * w[name + ".weight"]
            + w[name + ".bias"]
        )

    def pos(x):
        a = x[..., None] * np.exp(np.arange(0, 16, 2) * (-math.log(10000) / 16))
        return np.stack((np.sin(a), np.cos(a)), -1).reshape(*x.shape, 16)

    history, future, valid, hp, fp = tensors
    history = history.copy()
    if saved["arm"] == "NO_OBS_ATTN":
        history[..., 22:] = 0
    a = linear(history, "history")
    h = ln(a * ndtr(a) + pos(hp), "norm_h")
    a = linear(future, "future")
    q0 = a * ndtr(a) + pos(fp)
    v = linear(h, "value")
    if saved["arm"] == "POOL_MLP":
        context = (
            np.sum(v * valid[..., None], axis=1, keepdims=True)
            / valid.sum(1)[:, None, None]
        )
    else:
        b, length, _ = q0.shape
        q = (
            linear(ln(q0, "norm_q"), "query")
            .reshape(b, length, 2, 8)
            .transpose(0, 2, 1, 3)
        )
        k = linear(h, "key").reshape(b, h.shape[1], 2, 8).transpose(0, 2, 1, 3)
        v = v.reshape(b, h.shape[1], 2, 8).transpose(0, 2, 1, 3)
        logits = np.einsum("bhti,bhki->bhtk", q, k, optimize=False) / np.sqrt(8)
        logits = np.where(valid[:, None, None, :], logits, -np.inf)
        exp = np.exp(logits - np.max(logits, axis=-1, keepdims=True))
        attention = exp / exp.sum(-1, keepdims=True)
        context = (
            np.einsum("bhtk,bhki->bhti", attention, v, optimize=False)
            .transpose(0, 2, 1, 3)
            .reshape(b, length, 16)
        )
    z = q0 + linear(context, "proj")
    a = linear(ln(z, "norm_ff"), "ff1")
    z = z + linear(a * ndtr(a), "ff2")
    return baseline + linear(ln(z, "norm_out"), "head")[0] * saved["scaling"]["unit"]


def main(attempt):
    cfg = spec()
    root = ROOT / cfg["out"]
    out = root / attempt
    out.mkdir(exist_ok=False)
    receipt = dict(
        status="running",
        started_utc=utc(),
        values_checked=0,
        max_difference=0.0,
        checkpoint_count=0,
        fits_checked=0,
        updates_checked=0,
        new_fits=0,
        new_updates=0,
        physical_forwards=0,
    )

    def close(a, b, tol=1e-8):
        a, b = np.asarray(a, float), np.asarray(b, float)
        assert a.shape == b.shape, (a.shape, b.shape)
        assert np.isfinite(a).all() and np.isfinite(b).all()
        d = float(np.max(abs(a - b))) if a.size else 0.0
        assert d <= tol, (d, tol)
        receipt["values_checked"] += int(a.size)
        receipt["max_difference"] = max(receipt["max_difference"], d)

    try:
        setup(cfg)
        verify_implementation(cfg)
        receipt["source_files"] = guard()
        verify_lock(root / "training_complete.json")
        verify_lock(root / "scoring_lock.json")
        y = read_labels(ROOT / cfg["data"], 1461)
        forcing, dates = read_forcing(ROOT / cfg["data"], 1461)
        teachers = bank(cfg)
        for k, t in teachers.items():
            assert int(t["teacher_fit_prefix"]) == k
            np.testing.assert_array_equal(t["dates"], dates[: len(t["mean"])])
            close(t["forcing"], forcing[: len(t["mean"])], 0)
            for value in t.values():
                if np.issubdtype(value.dtype, np.number):
                    assert np.isfinite(value).all()
        summaries = pd.read_csv(root / "analysis/phase_summary.csv").set_index(
            ["origin", "method"]
        )
        points = pd.read_csv(root / "analysis/metrics_by_point.csv").set_index(
            ["origin", "method", "point"]
        )
        seedtable = pd.read_csv(root / "analysis/seed_summary.csv").set_index(
            ["origin", "method", "seed"]
        )
        daily = pd.read_csv(root / "analysis/daily_predictions.csv")
        gates = read_json(root / "analysis/effect_gates.json")
        pairings = read_json(root / "analysis/pairing.json")
        previous = None
        for n, end in zip(cfg["origins"], cfg["ends"]):
            folder = root / f"origin_{n}"
            verify_lock(folder / "issue_lock.json")
            t = teachers[teacher_id(n, True)]
            sc = read_json(folder / "scaling.json")
            raw = np.concatenate((t["x"][:n], y[:n] - y[0], y[:n] - t["mean"][:n]), 1)
            close(sc["mean"], raw.mean(0), 0)
            close(sc["std"], np.maximum(raw.std(0), 1e-6), 0)
            close(sc["unit"], np.maximum(y[:n].std(0), 1), 0)
            close(sc["y0"], y[0], 0)
            assert sc["fit_prefix"] == n
            test_input = independent_inputs(
                teachers, y[:n], [n], np.arange(1, 294)[None], sc, True
            )
            all_means = load_npz(folder / "means.npz")
            all_seeds = load_npz(folder / "seeds.npz")
            assert set(all_means) == set(cfg["arms"] + cfg["controls"])
            for arm in cfg["arms"]:
                stack = []
                for seed in cfg["seeds"]:
                    dest = folder / arm / f"seed_{seed}"
                    done = verify_lock(dest / "complete.json")
                    assert done["new_fits"] == 1 and done["updates"] == 200
                    receipt["fits_checked"] += 1
                    receipt["updates_checked"] += 200
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
                    for m, distances in zip(ms.ravel(), hs.reshape(-1, 16)):
                        teacher = teacher_id(m)
                        assert teacher <= m and m + max(distances) <= len(
                            teachers[teacher]["mean"]
                        )
                    for j in [0, 199]:
                        independent = independent_inputs(
                            teachers, y[:n], ms[j], hs[j], sc
                        )
                        actual = inputs(teachers, y[:n], ms[j], hs[j], sc)
                        for a, b in zip(independent, actual):
                            close(a, b.numpy(), 0)
                        expected = np.array(
                            [
                                (
                                    y[m + h - 1]
                                    - teachers[teacher_id(m)]["mean"][m + h - 1]
                                )
                                / sc["unit"]
                                for m, h in zip(ms[j], hs[j])
                            ]
                        )
                        close(
                            expected,
                            targets(teachers, y[:n], ms[j], hs[j], sc).numpy(),
                            0,
                        )
                    trace = [
                        json.loads(line)
                        for line in (dest / "training.jsonl").read_text().splitlines()
                    ]
                    assert [r["step"] for r in trace] == list(range(1, 201))
                    close(
                        [r["loss"] for r in trace],
                        [r["mse"] + r["penalty"] for r in trace],
                        1e-12,
                    )
                    assert all(np.isfinite(r["grad_norm"]) for r in trace)
                    for step in cfg["checkpoints"]:
                        model, scale, saved = reload(dest / f"e{step}.pt", cfg)
                        assert scale == sc and saved["step"] == step
                        assert (
                            saved["training_prefix"],
                            saved["arm"],
                            saved["seed"],
                        ) == (n, arm, seed)
                        assert saved["config_sha256"] == sha(CONFIG)
                        expected = np.load(dest / f"e{step}_mean.npy")
                        assert expected.shape == (293, 4)
                        close(
                            predict(model, teachers, y[:n], n, end, scale), expected, 0
                        )
                        close(
                            numpy_forward(saved, test_input, t["mean"][n:end]),
                            expected,
                            1e-7,
                        )
                        if step == 0:
                            close(expected, t["mean"][n:end], 0)
                        receipt["checkpoint_count"] += 1
                    stack.append(np.load(dest / "e200_mean.npy"))
                close(all_seeds[arm], np.stack(stack), 0)
                close(all_means[arm], np.mean(stack, 0), 0)
            old = ROOT / cfg["prior_out"] / f"origin_{n}"
            mapping = {
                B: B,
                "DRIFT1": "DRIFT1",
                "RR_COND": "RR_COND",
                "OLD_TRANSFORMER": "A1",
                "OLD_HALF": "A05",
                "OLD_REG1": "L1",
            }
            for method, oldname in mapping.items():
                phase = "lambda" if oldname == "L1" else "alpha"
                close(
                    all_means[method],
                    load_npz(old / f"{phase}_means.npz")[oldname][n:end],
                    0,
                )
                close(
                    all_seeds[method],
                    load_npz(old / f"{phase}_seeds.npz")[oldname][:, n:end],
                    0,
                )
            close(all_means[B], t["mean"][n:end], 0)
            close(
                all_means["DRIFT1"],
                y[n - 1] + np.arange(1, 294)[:, None] * (y[n - 1] - y[n - 2]),
                0,
            )
            if previous is not None:
                sigmas = load_npz(folder / "sigmas.npz")
                errors = load_npz(folder / "calibration_errors.npz")
                issued = load_npz(root / f"origin_{previous}/means.npz")
                cal = read_json(folder / "calibration.json")
                assert cal == dict(
                    previous_origin=previous,
                    start=previous + 90,
                    end=previous + 180,
                    labels_mature_before=n,
                    selection="none",
                    count=90,
                )
                assert previous + 180 <= n
                metrics, seed_metrics = {}, {}
                for method, mean in all_means.items():
                    expected_error = (
                        y[previous + 90 : previous + 180] - issued[method][90:180]
                    )
                    close(errors[method], expected_error, 0)
                    close(
                        sigmas[method],
                        np.maximum(np.sqrt((expected_error**2).mean(0)), 1e-6),
                        0,
                    )
                    if method in mapping:
                        oldname = mapping[method]
                        phase = "lambda" if oldname == "L1" else "alpha"
                        close(
                            sigmas[method],
                            load_npz(old / f"{phase}_sigmas.npz")[oldname],
                            0,
                        )
                    metrics[method] = independent_scores(y[n:end], mean, sigmas[method])
                    for key, values in metrics[method].items():
                        close(
                            points.loc[(n, method), key].reindex(cfg["points"]).values,
                            values,
                        )
                        close(summaries.loc[(n, method), key], values.mean())
                    assert summaries.loc[(n, method), "n_per_point"] == 293
                    seed_metrics[method] = []
                    for seed, sm in enumerate(all_seeds[method]):
                        metric = independent_scores(y[n:end], sm, sigmas[method])
                        seed_metrics[method].append(metric)
                        for key, values in metric.items():
                            close(seedtable.loc[(n, method, seed), key], values.mean())
                    part = daily[(daily.origin == n) & (daily.method == method)].copy()
                    assert len(part) == 1172
                    assert (part.issue_date == dates[n]).all()
                    for j, point in enumerate(cfg["points"]):
                        q = part[part.point == point].sort_values("target_index")
                        np.testing.assert_array_equal(q.date.values, dates[n:end])
                        close(q.target_index, np.arange(n, end), 0)
                        close(q.distance, np.arange(1, 294), 0)
                        close(q.observed, y[n:end, j])
                        close(q["mean"], mean[:, j])
                        close(q.sigma, np.full(293, sigmas[method][j]))
                for method, values in metrics.items():
                    assert (
                        independent_gate(values, metrics[B], cfg)
                        == gates[str(n)][method]
                    )
                records = [r for r in pairings if r["origin"] == n]
                assert len(records) == 5
                for record in records:
                    a, b = record["candidate"], record["reference"]
                    independent = independent_gate(metrics[a], metrics[b], cfg)
                    for key, value in independent.items():
                        assert value == record[key]
                    flags = [
                        all(
                            seed_metrics[a][s][k].mean() < seed_metrics[b][s][k].mean()
                            for k in ["mae", "rmse"]
                        )
                        for s in cfg["seeds"]
                    ]
                    assert (
                        flags == record["seed_flags"]
                        and sum(flags) == record["seed_both_improve"]
                    )
            previous = n
        assert (len(summaries), len(points), len(seedtable), len(daily)) == (
            27,
            108,
            81,
            31644,
        )
        events = [
            json.loads(s) for s in (root / "events.jsonl").read_text().splitlines()
        ]
        starts = [r for r in events if r["event"] == "fit_started"]
        ends = [r for r in events if r["event"] == "fit_completed"]
        issued = [r for r in events if r["event"] == "trajectory_issued"]
        assert len(starts) == len(ends) == 36 and len(issued) == 4
        full = [
            i
            for i, r in enumerate(events)
            if r["event"] == "label_prefix_read" and r["rows"] == 1461
        ]
        assert len(full) == 1
        assert all(events.index(r) < full[0] for r in issued)
        for r in issued:
            assert r["lock_sha256"] == sha(
                root / f"origin_{r['origin']}/issue_lock.json"
            )
        for n in cfg["origins"]:
            first = min(events.index(r) for r in starts if r["origin"] == n)
            relevant = [r for r in events[:first] if r["event"] == "label_prefix_read"]
            assert max(r["rows"] for r in relevant) == n
            assert all(
                r["time_utc"] < next(i["time_utc"] for i in issued if i["origin"] == n)
                for r in ends
                if r["origin"] == n
            )
        receipt.update(
            status="passed",
            finished_utc=utc(),
            events_checked=len(events),
            summary_rows=27,
            point_rows=108,
            seed_rows=81,
            daily_rows=31644,
            audit_script_sha256=sha(ROOT / "code/transformer_origin/audit.py"),
        )
    except Exception:
        receipt.update(
            status="failed", error=traceback.format_exc(), finished_utc=utc()
        )
        write_json(out / "receipt.json", receipt)
        raise
    write_json(out / "receipt.json", receipt)
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", default="independent_audit")
    main(parser.parse_args().attempt)
