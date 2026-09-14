"""Independent saved-model replay, horizon selection, RMS, scores and decisions."""

import json
import math
import traceback

import numpy as np
import pandas as pd
import torch
from scipy.special import ndtri

from sequence_conditional.audit import independently_score
from sequence_conditional.core import predict, reload_model
from sequence_conditional.independent import numpy_forward
from .core import (
    B,
    OLD,
    REG,
    HALF,
    ROOT,
    guard_sources,
    load_npz,
    read_json,
    read_labels,
    sha,
    spec,
    utc,
    verify_lock,
    write_json,
)


def independent_effect(c, b, cfg):
    e = cfg["effect"]
    mean, probability = {}, {}
    for k in ("mae", "rmse"):
        mean["average_" + k] = bool(
            c[k].mean() <= b[k].mean() * (1 - e["mean_relative_improvement"])
        )
    for k in ("crps", "interval_score90"):
        probability["average_" + k] = bool(
            c[k].mean() <= b[k].mean() * (1 - e["probability_relative_improvement"])
        )
    probability["coverage90_average"] = bool(
        c["coverage90"].mean() >= e["coverage90_average_min"]
    )
    for p, point in enumerate(cfg["points"]):
        for k in ("mae", "rmse"):
            mean[point + "_" + k] = bool(c[k][p] <= b[k][p] + e["point_mean_atol_mm"])
        for k in ("crps", "interval_score90"):
            probability[point + "_" + k] = bool(
                c[k][p] <= b[k][p] * (1 + e["point_probability_max_regression"])
            )
        probability[point + "_coverage90"] = bool(
            c["coverage90"][p] >= e["coverage90_point_min"]
        )
    return dict(
        mean_pass=all(mean.values()),
        probability_pass=all(probability.values()),
        joint_pass=all(mean.values()) and all(probability.values()),
        mean_checks=mean,
        probability_checks=probability,
    )


def main():
    cfg = spec()
    root = ROOT / cfg["out"]
    dest = root / "verification_v1"
    dest.mkdir(exist_ok=False)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    receipt = dict(
        status="running",
        time_utc=utc(),
        numerical_values_checked=0,
        max_abs_difference=0.0,
        model_checkpoints=0,
        ridge_reloads=0,
        new_fits=0,
        optimizer_updates=0,
        physical_forwards=0,
    )

    def close(a, b, tol=1e-8):
        a, b = np.asarray(a, float), np.asarray(b, float)
        assert a.shape == b.shape, (a.shape, b.shape)
        receipt["numerical_values_checked"] += a.size
        delta = float(np.max(abs(a - b))) if a.size else 0.0
        receipt["max_abs_difference"] = max(receipt["max_abs_difference"], delta)
        np.testing.assert_allclose(a, b, atol=tol, rtol=0)

    try:
        receipt["source_files_checked"] = guard_sources()
        for p, h in read_json(root / "implementation_lock.json")["files"].items():
            assert sha(ROOT / p) == h, p
        verify_lock(root / "implementation_verification/manifest.json")
        for phase in ("development", "final_exploratory"):
            for name in (
                "mean_lock.json",
                "distribution_lock.json",
                "scoring_lock.json",
            ):
                verify_lock(root / phase / name)
        verify_lock(root / "selection_lock.json")
        y = read_labels(ROOT / cfg["data"], 1461)
        stage = {}
        oldcfg = read_json(ROOT / "config/ootang_transformer_regularization.v1_0.json")
        for phase, (n, end) in cfg["stages"].items():
            data = load_npz(
                ROOT / cfg["reuse_tcn"] / f"implementation_verification/teacher_{n}.npz"
            )
            expected_dates = (
                pd.date_range("2016-07-01", periods=end)
                .strftime("%Y-%m-%d")
                .to_numpy(dtype="U10")
            )
            assert np.array_equal(data["dates"], expected_dates)
            scale = read_json(
                ROOT / cfg["reuse_regularization"] / phase / "scaling.json"
            )
            close(scale["unit"], np.maximum(np.std(y[:n] - y[0], axis=0), 1), 0)
            close(scale["x_mean"], data["x"][:n].mean(0), 0)
            close(scale["x_std"], np.maximum(data["x"][:n].std(0), 1e-6), 0)
            full = {B: data["mean"]}
            seed_values = {}
            for m, origin in ((OLD, "reuse_sequence"), (REG, "reuse_regularization")):
                members = []
                for seed in cfg["seeds"]:
                    d = ROOT / cfg[origin] / phase / m / f"seed_{seed}"
                    saved = torch.load(
                        d / "e400.pt", map_location="cpu", weights_only=True
                    )
                    assert saved["training_prefix"] == n
                    assert saved["seed"] == seed
                    assert saved.get("updates", saved.get("step")) == 400
                    stored = np.load(d / "e400_mean.npy")
                    close(numpy_forward(saved, data["x"], data["mean"]), stored, 1e-7)
                    model, scaling, _ = reload_model(d / "e400.pt", oldcfg)
                    close(
                        predict(model, scaling, OLD, data["x"], data["mean"]), stored, 0
                    )
                    members.append(stored)
                    seed_values[f"{m}__{seed}"] = stored
                    receipt["model_checkpoints"] += 1
                full[m] = np.stack(members).mean(0)
            # A distinct algebraic expression checks the saved shrinkage combination.
            full[HALF] = (full[B] + full[OLD]) / 2
            for seed in cfg["seeds"]:
                seed_values[f"{HALF}__{seed}"] = (
                    full[B] + seed_values[f"{OLD}__{seed}"]
                ) / 2
            means = {m: a[n:] for m, a in full.items()}
            means["DRIFT1"] = y[n - 1] + np.arange(1, end - n + 1)[:, None] * (
                y[n - 1] - y[n - 2]
            )
            ridge = load_npz(ROOT / cfg["reuse_tcn"] / phase / "ridge.npz")
            z = (data["x"] - ridge["x_mean"]) / ridge["x_std"]
            rr = ridge["y0"] + (z @ ridge["coef"] + ridge["intercept"]) * ridge["unit"]
            means["RR_COND"] = rr[n:]
            receipt["ridge_reloads"] += 1
            reused = load_npz(
                ROOT / cfg["reuse_regularization"] / phase / "reused_predictions.npz"
            )
            for m in reused:
                close(means[m], reused[m])
            stage[phase] = dict(
                means=means, full=full, seeds=seed_values, dates=expected_dates
            )
            if phase != "internal":
                d = root / phase
                for name, reference in (
                    ("means.npz", means),
                    ("full_means.npz", full),
                    ("seed_full_means.npz", seed_values),
                ):
                    disk = load_npz(d / name)
                    assert set(disk) == (
                        set(reference)
                        | ({"dates"} if name == "full_means.npz" else set())
                    )
                    for m, values in reference.items():
                        close(disk[m], values)

        (
            summary_rows,
            point_rows,
            seed_rows,
            distance_rows,
            transfer_rows,
            pairing_rows,
            amplitude_rows,
        ) = [], [], [], [], [], [], []
        reference_scores, seed_scores = {}, {}
        for phase in ("development", "final_exploratory"):
            n, end = cfg["stages"][phase]
            d = root / phase
            source = cfg["calibration"]["source"][phase]
            lo, hi = source["indices"]
            prefix = source["teacher_prefix"]
            assert lo > source["available_after_selection_index"] and hi <= n
            hist_h = list(range(lo - prefix + 1, hi - prefix + 1))
            issue = load_npz(d / "issued_distribution.npz")
            history = load_npz(d / "calibration_history.npz")
            close(history["indices"], np.arange(lo, hi), 0)
            close(history["horizons"], hist_h, 0)
            close(history["observed"], y[lo:hi], 0)
            units_old = np.maximum(np.std(y[:prefix] - y[0], axis=0), 1)
            units_new = np.maximum(np.std(y[:n] - y[0], axis=0), 1)
            close(history["unit_old"], units_old, 0)
            close(history["unit_new"], units_new, 0)
            summary = pd.read_csv(d / "summary.csv").set_index(["model", "rule"])
            points = pd.read_csv(d / "metrics_by_point.csv").set_index(
                ["model", "rule", "point"]
            )
            daily = pd.read_csv(d / "daily_predictions.csv")
            seeds = pd.read_csv(d / "seed_metrics.csv").set_index(
                ["model", "rule", "seed", "point"]
            )
            assert len(summary) == 18 and len(points) == 72 and len(seeds) == 108
            assert len(daily) == 18 * 4 * (end - n)
            assert not daily.duplicated(["model", "rule", "point", "index"]).any()
            expected_indices = {}
            for rule in cfg["rules"]:
                chosen = []
                for h in range(1, end - n + 1):
                    indices = (
                        list(range(hi - 90, hi))
                        if rule == "LAST90"
                        else sorted(
                            range(lo, hi),
                            key=lambda idx: (
                                abs(idx - prefix + 1 - h),
                                idx - prefix + 1,
                            ),
                        )[:90]
                    )
                    assert (
                        len(set(indices)) == 90
                        and min(indices) >= lo
                        and max(indices) < n
                    )
                    chosen.append(indices)
                    distances = np.array(indices) - prefix + 1
                    distance_rows.append(
                        dict(
                            phase=phase,
                            rule=rule,
                            horizon=h,
                            count=90,
                            earliest_index=min(indices),
                            latest_index=max(indices),
                            minimum_history_distance=int(distances.min()),
                            maximum_history_distance=int(distances.max()),
                            mean_absolute_distance_gap=float(
                                np.mean(abs(distances - h))
                            ),
                            horizon_outside_available_range=bool(
                                h < min(hist_h) or h > max(hist_h)
                            ),
                        )
                    )
                expected_indices[rule] = np.array(chosen)
                close(issue[rule + "__history_indices"], chosen, 0)
            for m in cfg["methods"]:
                means = stage[phase]["means"][m]
                close(issue[m + "__mean"], means)
                old_start = cfg["stages"][source["phase"]][0]
                hist_mu = stage[source["phase"]]["means"][m][
                    lo - old_start : hi - old_start
                ]
                error = hist_mu - y[lo:hi]
                close(history[m + "__mean"], hist_mu)
                close(history[m + "__error"], error)
                for rule in cfg["rules"]:
                    indices = expected_indices[rule]
                    factor = (
                        units_new / units_old if rule == "DIST90_UNIT" else np.ones(4)
                    )
                    # Python fsum checks RMS independently of NumPy's production reduction.
                    sd = np.array(
                        [
                            [
                                max(
                                    math.sqrt(
                                        math.fsum(
                                            float(error[j - lo, p]) ** 2 for j in row
                                        )
                                        / 90
                                    )
                                    * factor[p],
                                    1e-6,
                                )
                                for p in range(4)
                            ]
                            for row in indices
                        ]
                    )
                    close(issue[m + "__" + rule + "__sigma"], sd)
                    if phase == "development" and rule == "DIST90":
                        close(
                            issue[m + "__DIST90__sigma"],
                            issue[m + "__LAST90__sigma"],
                            0,
                        )
                    metrics = independently_score(y[n:end], means, sd)
                    reference_scores[(phase, m, rule)] = metrics
                    for k, a in metrics.items():
                        close(summary.loc[(m, rule), k], a.mean())
                        for p, point in enumerate(cfg["points"]):
                            close(points.loc[(m, rule, point), k], a[p])
                    summary_rows.append(
                        dict(
                            phase=phase,
                            model=m,
                            rule=rule,
                            n_per_point=end - n,
                            **{k: float(v.mean()) for k, v in metrics.items()},
                        )
                    )
                    for p, point in enumerate(cfg["points"]):
                        point_rows.append(
                            dict(
                                phase=phase,
                                model=m,
                                rule=rule,
                                point=point,
                                n=end - n,
                                **{k: float(v[p]) for k, v in metrics.items()},
                            )
                        )
                        frame = daily[
                            (daily.model == m)
                            & (daily.rule == rule)
                            & (daily.point == point)
                        ].sort_values("index")
                        close(frame["index"].values, np.arange(n, end), 0)
                        close(frame.horizon.values, np.arange(1, end - n + 1), 0)
                        assert np.array_equal(
                            frame.date.values, stage[phase]["dates"][n:]
                        )
                        for k, val in (
                            ("observed", y[n:end, p]),
                            ("mean", means[:, p]),
                            ("sigma", sd[:, p]),
                        ):
                            close(frame[k].values, val)
                        for level in (80, 90, 95):
                            z = float(ndtri((1 + level / 100) / 2))
                            close(
                                frame[f"lower{level}"].values,
                                means[:, p] - z * sd[:, p],
                            )
                            close(
                                frame[f"upper{level}"].values,
                                means[:, p] + z * sd[:, p],
                            )
                        transfer_rows.append(
                            dict(
                                phase=phase,
                                model=m,
                                rule=rule,
                                point=point,
                                history_teacher_prefix=prefix,
                                current_teacher_prefix=n,
                                history_rows=hi - lo,
                                historical_bias=float(error[:, p].mean()),
                                historical_rms=float(
                                    np.sqrt(np.mean(error[:, p] ** 2))
                                ),
                                unit_ratio=float(units_new[p] / units_old[p]),
                                sigma_first=float(sd[0, p]),
                                sigma_last=float(sd[-1, p]),
                                sigma_average=float(sd[:, p].mean()),
                                forecast_rmse=float(metrics["rmse"][p]),
                            )
                        )
                    if m in (OLD, REG, HALF):
                        for seed in cfg["seeds"]:
                            sr = independently_score(
                                y[n:end], stage[phase]["seeds"][f"{m}__{seed}"][n:], sd
                            )
                            seed_scores[(phase, m, rule, seed)] = {
                                k: float(v.mean()) for k, v in sr.items()
                            }
                            seed_rows.append(
                                dict(
                                    phase=phase,
                                    model=m,
                                    rule=rule,
                                    seed=seed,
                                    **seed_scores[(phase, m, rule, seed)],
                                )
                            )
                            for k, values in sr.items():
                                for p, point in enumerate(cfg["points"]):
                                    close(
                                        seeds.loc[(m, rule, seed, point), k], values[p]
                                    )
            original = load_npz(
                ROOT / cfg["reuse_regularization"] / phase / "issued_distribution.npz"
            )
            for m in cfg["methods"][:-1]:
                close(issue[m + "__mean"], original[m + "__mean"], 0)
                close(
                    issue[m + "__LAST90__sigma"],
                    np.broadcast_to(original[m + "__sigma"], (end - n, 4)),
                    0,
                )
            actual_gates = read_json(d / "effect_gates.json")
            for m in cfg["methods"]:
                for rule in cfg["rules"]:
                    c = reference_scores[(phase, m, rule)]
                    for field, ref in (
                        ("vs_bplus_same_rule", (B, rule)),
                        ("vs_bplus_last90", (B, "LAST90")),
                        ("vs_own_last90", (m, "LAST90")),
                    ):
                        assert actual_gates[m + "__" + rule][
                            field
                        ] == independent_effect(c, reference_scores[(phase, *ref)], cfg)
            for a, b, label in (
                (REG, HALF, "reg_vs_half"),
                (HALF, OLD, "half_vs_original"),
                (REG, OLD, "reg_vs_original"),
            ):
                ar = reference_scores[(phase, a, "LAST90")]
                br = reference_scores[(phase, b, "LAST90")]
                paired = [
                    all(
                        seed_scores[(phase, a, "LAST90", s)][k]
                        < seed_scores[(phase, b, "LAST90", s)][k]
                        for k in ("mae", "rmse")
                    )
                    for s in cfg["seeds"]
                ]
                pairing_rows.append(
                    dict(
                        phase=phase,
                        pair=label,
                        candidate=a,
                        reference=b,
                        mae_relative_change=float(
                            ar["mae"].mean() / br["mae"].mean() - 1
                        ),
                        rmse_relative_change=float(
                            ar["rmse"].mean() / br["rmse"].mean() - 1
                        ),
                        seeds_both_mean_improve=sum(paired),
                        seed0=paired[0],
                        seed1=paired[1],
                        seed2=paired[2],
                        descriptively_within_one_percent=bool(
                            all(
                                abs(ar[k].mean() / br[k].mean() - 1) <= 0.01
                                for k in ("mae", "rmse")
                            )
                        ),
                    )
                )
            for p, point in enumerate(cfg["points"]):
                for scope, sl in (
                    ("fitting", slice(0, n)),
                    ("forecast", slice(n, end)),
                ):
                    full = stage[phase]["full"]
                    difference = full[REG][sl, p] - full[HALF][sl, p]
                    corr = full[OLD][sl, p] - full[B][sl, p]
                    amplitude_rows.append(
                        dict(
                            phase=phase,
                            point=point,
                            scope=scope,
                            reg_minus_half_rms=float(np.sqrt(np.mean(difference**2))),
                            original_correction_rms=float(np.sqrt(np.mean(corr**2))),
                            ratio=float(
                                np.sqrt(np.mean(difference**2))
                                / max(np.sqrt(np.mean(corr**2)), 1e-12)
                            ),
                        )
                    )

        selected = read_json(root / "selection.json")

        def ranking(keys, order):
            options = list(order)
            for k in keys:
                best = min(
                    reference_scores[("development", *v)][k].mean() for v in options
                )
                options = [
                    v
                    for v in options
                    if reference_scores[("development", *v)][k].mean() <= best + 1e-12
                ]
            return options[0]

        assert (
            selected["mean_winner"]
            == ranking(["mae", "rmse"], [(m, "LAST90") for m in cfg["methods"]])[0]
        )
        assert selected["probability_winner"] == "__".join(
            ranking(
                ["crps", "interval_score90"],
                [(m, r) for m in cfg["methods"] for r in cfg["rules"]],
            )
        )
        c = reference_scores[("development", REG, "LAST90")]
        b = reference_scores[("development", HALF, "LAST90")]
        expected = {
            "average_" + k: bool(c[k].mean() <= 0.99 * b[k].mean())
            for k in ("mae", "rmse")
        }
        for p, point in enumerate(cfg["points"]):
            for k in ("mae", "rmse"):
                expected[point + "_" + k] = bool(c[k][p] <= b[k][p] + 1e-6)
        paired = [
            all(
                seed_scores[("development", REG, "LAST90", s)][k]
                < seed_scores[("development", HALF, "LAST90", s)][k]
                for k in ("mae", "rmse")
            )
            for s in cfg["seeds"]
        ]
        expected["two_paired_seeds"] = sum(paired) >= 2
        expected["bplus_mean_gate"] = independent_effect(
            c, reference_scores[("development", B, "LAST90")], cfg
        )["mean_pass"]
        assert selected["search_trigger_checks"] == expected
        assert selected["seed_reg_beats_half"] == paired
        assert selected["search_recommendation"] == all(expected.values())
        final_selection = read_json(
            root / "final_exploratory/frozen_selection_evaluation.json"
        )
        assert (
            final_selection["selection"] == selected
            and final_selection["selection_changed"] is False
        )
        for label, key in (
            ("mean", (selected["mean_winner"], "LAST90")),
            ("probability", tuple(selected["probability_winner"].split("__"))),
        ):
            for k, values in reference_scores[("final_exploratory", *key)].items():
                close(final_selection[label][k], values.mean())
        events = [
            json.loads(line)
            for line in (root / "events.jsonl").read_text().splitlines()
        ]
        assert not any(e["event"] in ("run_error", "fit_started") for e in events)
        assert len(events) == 13, len(events)
        for phase, (n, end) in [
            (p, cfg["stages"][p]) for p in ("development", "final_exploratory")
        ]:

            def pos(kind):
                return next(
                    i
                    for i, e in enumerate(events)
                    if e["event"] == kind and e.get("phase") == phase
                )

            start = pos("phase_started")
            distribution = pos("distribution_locked")
            completed = pos("phase_completed")
            assert start < pos("means_locked") < distribution < completed
            reads = [
                (i, e)
                for i, e in enumerate(events)
                if start < i < completed and e["event"] == "label_prefix_read"
            ]
            assert (
                len(reads) == 2
                and reads[0][1]["rows"] == n
                and reads[1][1]["rows"] == end
            )
            assert reads[0][0] < distribution < reads[1][0]
        selection_pos = next(
            i
            for i, e in enumerate(events)
            if e["event"] == "development_selection_locked"
        )
        final_start = next(
            i
            for i, e in enumerate(events)
            if e["event"] == "phase_started" and e["phase"] == "final_exploratory"
        )
        assert selection_pos < final_start
        for name, rows in (
            ("phase_summary", summary_rows),
            ("metrics_by_point", point_rows),
            ("seed_summary", seed_rows),
            ("calibration_distance_support", distance_rows),
            ("calibration_transfer", transfer_rows),
            ("shrinkage_pairing", pairing_rows),
            ("correction_difference", amplitude_rows),
        ):
            pd.DataFrame(rows).to_csv(dest / (name + ".csv"), index=False)
        receipt.update(
            status="passed",
            time_utc=utc(),
            event_records=len(events),
            distributions_checked=36,
            summary_rows=len(summary_rows),
            point_rows=len(point_rows),
            seed_summary_rows=len(seed_rows),
            daily_rows_checked=18 * 4 * (376 + 293),
            distance_support_rows=len(distance_rows),
            final_selection_unchanged=True,
            calibration_has_no_current_target_labels=True,
            old_last90_predictions_preserved=True,
            source_guard_after=guard_sources(),
        )
    except Exception:
        receipt.update(
            status="failed", time_utc=utc(), traceback=traceback.format_exc()
        )
        write_json(dest / "receipt.json", receipt)
        raise
    write_json(dest / "receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
