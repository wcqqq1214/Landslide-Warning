"""Independent raw-input, fixed-checkpoint and diagnostic-statistic audit."""

import argparse
import json
import traceback
import numpy as np
import pandas as pd
import torch
from tide_direct.audit import independent_input
from tide_direct.numpy_model import forward
from transformer_temporal.audit import independent_scores
from . import core as c


def main(attempt):
    cfg, o = c.spec(), c.o
    o.setup(cfg)
    sources = c.guard()
    root = c.ROOT / cfg["out"]
    out = root / attempt
    out.mkdir(exist_ok=False)
    data_dir = root / o.read_json(root / "diagnostic_lock.json")["attempt"]
    o.verify_lock(data_dir / "lock.json")
    checks = []
    receipt = dict(status="running")

    def close(name, a, b, tol=1e-8):
        a, b = np.asarray(a, float), np.asarray(b, float)
        assert a.shape == b.shape and np.array_equal(np.isnan(a), np.isnan(b)), name
        valid = ~np.isnan(a)
        assert np.isfinite(a[valid]).all() and np.isfinite(b[valid]).all(), name
        diff = float(np.max(abs(a[valid] - b[valid]), initial=0))
        assert diff <= tol, (name, diff, tol)
        checks.append(
            dict(
                name=name,
                values=int(a.size),
                max_difference=diff,
                unavailable=int((~valid).sum()),
            )
        )

    def check_table(name, records, keys):
        expected = pd.DataFrame(records).set_index(keys).sort_index()
        actual = (
            pd.read_csv(
                data_dir / (name + ".csv"),
                float_precision="round_trip",
                dtype={"seed": str},
            )
            .set_index(keys)
            .sort_index()
        )
        assert expected.index.equals(actual.index), name
        assert set(expected.columns) == set(actual.columns), (
            name,
            expected.columns,
            actual.columns,
        )
        for col in expected:
            close(name + "/" + col, actual[col].values, expected[col].values)
        return expected, actual

    def score_records(truth, mu, sigma, meta):
        records = []
        for b in cfg["bins"]:
            ix = np.arange(b["start"] - 1, b["end"])
            values = independent_scores(truth[ix], mu[ix], sigma)
            e = mu[ix] - truth[ix]
            for p, point in enumerate(cfg["points"]):
                records.append(
                    dict(
                        **meta,
                        bin=b["name"],
                        point=point,
                        n=len(ix),
                        **{k: float(v[p]) for k, v in values.items()},
                        bias=float(e[:, p].mean()),
                        mse=float(np.mean(e[:, p] ** 2)),
                        error_variance=float(np.var(e[:, p], ddof=0)),
                        probability_available=sigma is not None,
                    )
                )
        return records

    try:
        y = o.read_labels(c.ROOT / cfg["data"], 1461)
        f, _ = o.read_forcing(c.ROOT / cfg["data"], 1461)
        teachers = o.bank(c.prior.spec())
        replay = o.load_npz(data_dir / "checkpoint_forecasts.npz")
        visibility = o.load_npz(data_dir / "visibility_forecasts.npz")
        expected_weights = []
        train_rows = []
        checkpoint_rows = []
        issued_rows = []
        decomp_rows = []
        tail_rows = []
        sensitivity_rows = []
        total = 0
        original_input_origins = 0
        for n in cfg["origins"]:
            truth = y[n : n + 293]
            ms = np.arange(432, n)
            lengths = np.minimum(293, n - ms)
            target = np.zeros((len(ms), 293, 4))
            mask = np.zeros((len(ms), 293), bool)
            source = c.ROOT / cfg["prior_out"] / f"origin_{n}"
            means, seeds = [
                o.load_npz(source / (k + ".npz")) for k in ["means", "seeds"]
            ]
            sigma = o.load_npz(source / "sigmas.npz") if n != 612 else {}
            step_means = {k: [] for k in cfg["steps"]}
            ck0 = torch.load(
                c.folder(cfg, n, 0) / "e0.pt", map_location="cpu", weights_only=True
            )
            sc = ck0["scaling"]
            unit = np.asarray(sc["unit"])
            close(f"n{n} point scaling", unit, np.maximum(y[:n].std(0), 1), 0)
            histories = []
            covariates = []
            for i, m in enumerate(ms):
                hi, xi = independent_input(
                    c.prior.old.spec(),
                    teachers,
                    y[:n],
                    f[:n],
                    int(m),
                    sc,
                    "TiDE_PHYS",
                    n,
                )
                xi[..., 7:10] = 0
                histories.append(hi)
                covariates.append(xi)
                target[i, : lengths[i]] = (y[m : m + lengths[i]] - y[m - 1]) / unit
                mask[i, : lengths[i]] = True
            hi, xi = np.stack(histories), np.stack(covariates)
            production = c.inputs(teachers, y[:n], f, n, sc, ms)
            for key, a, b in zip(
                ["history", "covariates", "targets", "maturity"],
                production,
                [hi, xi, target, mask],
            ):
                close(f"n{n} full train {key}", a, b)
            del production, histories, covariates
            original_input_origins += len(ms)
            hfull, xfull = independent_input(
                c.prior.old.spec(),
                teachers,
                y[:n],
                f[: n + 293],
                n,
                sc,
                "TiDE_PHYS",
                n + 293,
            )
            xfull[..., 7:10] = 0
            for seed in cfg["seeds"]:
                schedule = np.load(c.folder(cfg, n, seed) / "schedule.npz")["origins"]
                close(
                    f"n{n}s{seed} schedule",
                    schedule,
                    np.random.default_rng(seed).integers(432, n, (400, 8)),
                    0,
                )
                for step in cfg["steps"]:
                    counts = np.bincount(
                        schedule[:step].ravel() - 432, minlength=len(ms)
                    )
                    for h in range(1, 294):
                        eligible = lengths >= h
                        row = dict(
                            origin=n,
                            seed=seed,
                            step=step,
                            horizon=h,
                            eligible_count=int(eligible.sum()),
                            expected_loss_weight=float(
                                np.sum(1 / lengths[eligible]) / len(ms)
                            ),
                            actual_loss_weight=float(
                                np.sum(counts[eligible] / lengths[eligible])
                                / (step * 8)
                            )
                            if step
                            else 0.0,
                            sampled_terms=int(counts[eligible].sum()),
                            sampled_unique_origins=int(
                                np.count_nonzero(counts[eligible])
                            ),
                            updates_with_horizon=int(
                                np.sum(np.any(n - schedule[:step] >= h, axis=1))
                            ),
                        )
                        expected_weights.append(row)
                    ck = torch.load(
                        c.folder(cfg, n, seed) / f"e{step}.pt",
                        map_location="cpu",
                        weights_only=True,
                    )
                    assert (
                        ck["seed"] == seed
                        and ck["step"] == step
                        and ck["scaling"] == sc
                    )
                    state = ck["state_dict"]
                    assert sum(v.numel() for v in state.values()) == 110392
                    pred = np.concatenate(
                        [
                            forward(state, hi[i : i + 16], xi[i : i + 16])[:, 1:]
                            for i in range(0, len(ms), 16)
                        ]
                    )
                    err = pred - target
                    for b in cfg["bins"]:
                        ix = np.arange(b["start"] - 1, b["end"])
                        active = mask[:, ix]
                        for p, point in enumerate(cfg["points"]):
                            e = (err[:, ix, p] * unit[p])[active]
                            objective = np.sum(
                                np.where(active, err[:, ix, p] ** 2, 0)
                                / lengths[:, None]
                            ) / (len(ms) * 4)
                            train_rows.append(
                                dict(
                                    origin=n,
                                    seed=str(seed),
                                    step=step,
                                    bin=b["name"],
                                    point=point,
                                    n_terms=len(e),
                                    supported_origins=int(
                                        np.count_nonzero(active.sum(1))
                                    ),
                                    mae=float(np.sum(abs(e)) / len(e))
                                    if len(e)
                                    else np.nan,
                                    rmse=float(np.sqrt(np.sum(e**2) / len(e)))
                                    if len(e)
                                    else np.nan,
                                    bias=float(np.sum(e) / len(e))
                                    if len(e)
                                    else np.nan,
                                    normalized_mse=float(
                                        np.sum(e**2) / len(e) / unit[p] ** 2
                                    )
                                    if len(e)
                                    else np.nan,
                                    objective_contribution=float(objective),
                                )
                            )
                    q = forward(state, hfull[None], xfull[None])[0]
                    close(f"n{n}s{seed}e{step} h0", q[0], np.zeros(4), 0)
                    mu = y[n - 1] + q[1:] * unit
                    close(
                        f"n{n}s{seed}e{step} source",
                        mu,
                        np.load(c.folder(cfg, n, seed) / f"e{step}_mean.npy"),
                    )
                    close(
                        f"n{n}s{seed}e{step} replay",
                        mu,
                        replay[f"n{n}_s{seed}_e{step}"],
                    )
                    step_means[step].append(mu)
                    checkpoint_rows += score_records(
                        truth, mu, None, dict(origin=n, seed=str(seed), step=step)
                    )
                    if step == 0:
                        close(f"n{n}s{seed} e0", q, np.zeros_like(q), 0)
                    if step == 400:
                        close(f"n{n}s{seed} original KIN", mu, seeds["TiDE_KIN"][seed])
                        indices = np.r_[
                            np.arange(n - 180, n), np.arange(n - 1, n + 293)
                        ]
                        for d in cfg["visibility"]:
                            masked = xfull.copy()
                            masked[:, indices >= n + d, :10] = 0
                            masked[:, indices >= n + d, 11] = 0
                            qp = forward(state, hfull[None], masked[None])[0]
                            pm = y[n - 1] + qp[1:] * unit
                            close(
                                f"n{n}s{seed}d{d} independent mask prediction",
                                pm,
                                visibility[f"n{n}_s{seed}_d{d}"],
                            )
                            if d == 293:
                                close(
                                    f"n{n}s{seed} full identity",
                                    visibility[f"n{n}_s{seed}_d{d}"],
                                    seeds["TiDE_KIN"][seed],
                                    0,
                                )
                    total += 1
            for step, mus in step_means.items():
                checkpoint_rows += score_records(
                    truth,
                    np.mean(mus, 0),
                    None,
                    dict(origin=n, seed="ensemble", step=step),
                )
            for method in cfg["methods"]:
                close(f"n{n}/{method} ensemble", means[method], seeds[method].mean(0))
                for seed, mu in [("ensemble", means[method])] + [
                    (str(s), seeds[method][s]) for s in cfg["seeds"]
                ]:
                    issued_rows += score_records(
                        truth,
                        mu,
                        sigma.get(method),
                        dict(origin=n, method=method, seed=seed),
                    )
                    for p, point in enumerate(cfg["points"]):
                        e = mu[:, p] - truth[:, p]
                        ss = float(sum(e * e))
                        tailss = float(sum(e[180:] ** 2))
                        tail_rows.append(
                            dict(
                                origin=n,
                                method=method,
                                seed=seed,
                                point=point,
                                total_sse=ss,
                                tail_sse=tailss,
                                tail_fraction=tailss / ss if ss else np.nan,
                            )
                        )
                for b in cfg["bins"]:
                    ix = np.arange(b["start"] - 1, b["end"])
                    for p, point in enumerate(cfg["points"]):
                        err = means[method][ix, p] - truth[ix, p]
                        individual = seeds[method][:, ix, p]
                        mse = float(np.mean(err * err))
                        bias = float(np.mean(err) ** 2)
                        variance = float(np.mean((err - err.mean()) ** 2))
                        smse = float(np.mean((individual - truth[ix, p]) ** 2))
                        spread = float(
                            np.mean((individual - means[method][ix, p]) ** 2)
                        )
                        close(
                            f"bias identity n{n}/{method}/{point}/{b['name']}",
                            mse,
                            bias + variance,
                        )
                        close(
                            f"seed identity n{n}/{method}/{point}/{b['name']}",
                            smse,
                            mse + spread,
                        )
                        decomp_rows.append(
                            dict(
                                origin=n,
                                method=method,
                                bin=b["name"],
                                point=point,
                                ensemble_mse=mse,
                                bias_squared=bias,
                                error_variance=variance,
                                mean_seed_mse=smse,
                                forecast_spread_mse=spread,
                                ensemble_reduction_fraction=spread / smse
                                if smse
                                else np.nan,
                            )
                        )
            for d in cfg["visibility"]:
                pm = np.stack([visibility[f"n{n}_s{s}_d{d}"] for s in cfg["seeds"]])
                baseline = seeds["TiDE_KIN"]
                for seed, a, b in [("ensemble", pm.mean(0), baseline.mean(0))] + [
                    (str(s), pm[s], baseline[s]) for s in cfg["seeds"]
                ]:
                    delta = (a - b)[:d]
                    for p, point in enumerate(cfg["points"]):
                        e = delta[:, p]
                        sensitivity_rows.append(
                            dict(
                                origin=n,
                                visible=d,
                                seed=seed,
                                point=point,
                                common_horizons=d,
                                rms_change=float(np.sqrt(sum(e * e) / len(e))),
                                mean_change=float(sum(e) / len(e)),
                                mae_change=float(sum(abs(e)) / len(e)),
                                max_abs_change=float(max(abs(e))),
                                performance_comparison=False,
                            )
                        )
            print(f"Independent replay {n}: {total}/60", flush=True)
        # CSV seed keys are textual; weights use the same fixed seed identities.
        for r in expected_weights:
            r["seed"] = str(r["seed"])
        tables = {
            "supervision_weights": (
                expected_weights,
                ["origin", "seed", "step", "horizon"],
            ),
            "training_by_point": (
                train_rows,
                ["origin", "seed", "step", "bin", "point"],
            ),
            "checkpoint_forecast_by_point": (
                checkpoint_rows,
                ["origin", "seed", "step", "bin", "point"],
            ),
            "issued_by_point": (
                issued_rows,
                ["origin", "method", "seed", "bin", "point"],
            ),
            "mse_decomposition": (decomp_rows, ["origin", "method", "bin", "point"]),
            "tail_sse": (tail_rows, ["origin", "method", "seed", "point"]),
            "visibility_sensitivity": (
                sensitivity_rows,
                ["origin", "visible", "seed", "point"],
            ),
        }
        for name, (records, keys) in tables.items():
            check_table(name, records, keys)
        for target, records, groups in [
            ("training_summary", train_rows, ["origin", "seed", "step", "bin"]),
            (
                "checkpoint_forecast_summary",
                checkpoint_rows,
                ["origin", "seed", "step", "bin"],
            ),
            ("issued_summary", issued_rows, ["origin", "method", "seed", "bin"]),
        ]:
            frame = pd.DataFrame(records)
            numeric = [
                k
                for k in frame.select_dtypes(include=np.number)
                if k not in groups and k not in ["n", "n_terms", "supported_origins"]
            ]
            aggregate = frame.groupby(groups, sort=False)[numeric].mean().reset_index()
            if target == "training_summary":
                aggregate["objective_contribution"] *= 4
            check_table(target, aggregate.to_dict("records"), groups)
        # Reproduce every original selected-method primary-window metric.
        old = pd.read_csv(
            c.ROOT / cfg["prior_out"] / "analysis/metrics_by_point.csv",
            float_precision="round_trip",
        ).set_index(["origin", "method", "point"])
        issued = pd.DataFrame(issued_rows).set_index(
            ["origin", "method", "seed", "bin", "point"]
        )
        for n in cfg["primary_origins"]:
            for method in cfg["methods"]:
                for pt in cfg["points"]:
                    for metric in [
                        "mae",
                        "rmse",
                        "crps",
                        "coverage90",
                        "width90",
                        "interval_score90",
                    ]:
                        close(
                            f"old primary {n}/{method}/{pt}/{metric}",
                            issued.loc[(n, method, "ensemble", "all", pt), metric],
                            old.loc[(n, method, pt), metric],
                        )
        assert total == 60 and original_input_origins == 1816 and len(visibility) == 48
        c.guard()
        receipt.update(
            status="passed",
            source_files=sources,
            checkpoints=total,
            original_training_origins=original_input_origins,
            training_origin_evaluations=27240,
            visibility_seed_paths=48,
            checks=len(checks),
            values=sum(v["values"] for v in checks),
            max_difference=max(v["max_difference"] for v in checks),
            unavailable_cells_verified=sum(v["unavailable"] for v in checks),
            original_forecasts_unchanged=True,
            new_fits=0,
            optimizer_updates=0,
            new_bplus_fits=0,
            new_physical_forwards=0,
            bootstrap_probability="unavailable",
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
                for p in (c.ROOT / "code/tide_kin_diagnostic").glob("*.py")
            },
        )
    o.lock(out, "lock.json", list(out.glob("*")), status="passed")
    o.write_json(
        root / "audit_lock.json",
        dict(
            time_utc=o.utc(),
            status="passed",
            attempt=attempt,
            files={
                str((out / "lock.json").relative_to(root)): o.sha(out / "lock.json")
            },
        ),
    )
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", default="audit_v1")
    main(parser.parse_args().attempt)
