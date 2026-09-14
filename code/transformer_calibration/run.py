"""Issue complete saved means and frozen distributions before target scoring."""

import argparse
import traceback

import numpy as np
import pandas as pd

from .core import (
    B,
    OLD,
    REG,
    HALF,
    ROOT,
    calibration,
    check_deadline,
    choose,
    effect,
    event,
    guard_sources,
    load_npz,
    lock,
    read_json,
    read_labels,
    scores,
    sha,
    shrink,
    spec,
    summarize,
    unit,
    utc,
    verify_lock,
    write_json,
)


def assemble(cfg, phase):
    n, end = cfg["stages"][phase]
    reused = ROOT / cfg["reuse_regularization"] / phase
    teacher = load_npz(
        ROOT / cfg["reuse_tcn"] / f"implementation_verification/teacher_{n}.npz"
    )
    old = load_npz(ROOT / cfg["reuse_sequence"] / phase / "ensemble_selected.npz")[OLD]
    reg = load_npz(reused / "ensemble_selected.npz")[REG]
    full = {B: teacher["mean"], OLD: old, REG: reg}
    full[HALF] = shrink(full[B], old, cfg["half_weight"])
    means = load_npz(reused / "reused_predictions.npz")
    means.update({m: a[n:] for m, a in full.items()})
    seeds = {}
    for seed in cfg["seeds"]:
        for method, origin in ((OLD, "reuse_sequence"), (REG, "reuse_regularization")):
            seeds[f"{method}__{seed}"] = np.load(
                ROOT / cfg[origin] / phase / method / f"seed_{seed}/e400_mean.npy"
            )
        seeds[f"{HALF}__{seed}"] = shrink(
            full[B], seeds[f"{OLD}__{seed}"], cfg["half_weight"]
        )
    assert set(means) == set(cfg["methods"])
    assert all(a.shape == (end - n, 4) and np.isfinite(a).all() for a in means.values())
    assert all(a.shape == (end, 4) for a in full.values())
    return means, full, seeds, teacher["dates"]


def label_prefix(cfg, root, end, purpose):
    event(root, "label_prefix_read", rows=end, last_index=end - 1, purpose=purpose)
    return read_labels(ROOT / cfg["data"], end)


def score_phase(cfg, dest, y, means, seeds, issue, dates):
    phase = dest.name
    n, end = cfg["stages"][phase]
    summary, point, daily, seed_rows = [], [], [], []
    by_key = {}
    for method in cfg["methods"]:
        for rule in cfg["rules"]:
            key = method + "__" + rule
            sigma = issue[key + "__sigma"]
            rows = scores(y, means[method], sigma)
            by_key[key] = rows
            point.extend(dict(phase=phase, model=method, rule=rule, **r) for r in rows)
            summary.append(
                dict(phase=phase, model=method, rule=rule, **summarize(rows))
            )
            for p, name in enumerate(cfg["points"]):
                frame = pd.DataFrame(
                    dict(
                        phase=phase,
                        model=method,
                        rule=rule,
                        point=name,
                        index=np.arange(n, end),
                        date=dates[n:],
                        horizon=np.arange(1, end - n + 1),
                        observed=y[:, p],
                        mean=means[method][:, p],
                        sigma=sigma[:, p],
                    )
                )
                for level, z in (
                    (80, 1.2815515655446004),
                    (90, 1.6448536269514722),
                    (95, 1.959963984540054),
                ):
                    frame[f"lower{level}"] = frame["mean"] - z * frame.sigma
                    frame[f"upper{level}"] = frame["mean"] + z * frame.sigma
                daily.append(frame)
            if method in (OLD, REG, HALF):
                for seed in cfg["seeds"]:
                    sr = scores(y, seeds[f"{method}__{seed}"][n:], sigma)
                    seed_rows.extend(
                        dict(phase=phase, model=method, rule=rule, seed=seed, **r)
                        for r in sr
                    )
    pd.DataFrame(summary).to_csv(dest / "summary.csv", index=False)
    pd.DataFrame(point).to_csv(dest / "metrics_by_point.csv", index=False)
    pd.concat(daily, ignore_index=True).to_csv(
        dest / "daily_predictions.csv", index=False
    )
    pd.DataFrame(seed_rows).to_csv(dest / "seed_metrics.csv", index=False)
    decisions = {}
    for method in cfg["methods"]:
        for rule in cfg["rules"]:
            key = method + "__" + rule
            decisions[key] = {
                "vs_bplus_same_rule": effect(by_key[key], by_key[B + "__" + rule], cfg),
                "vs_bplus_last90": effect(by_key[key], by_key[B + "__LAST90"], cfg),
                "vs_own_last90": effect(by_key[key], by_key[method + "__LAST90"], cfg),
            }
    write_json(dest / "effect_gates.json", decisions)
    return by_key


def selection(cfg, root, metrics, seeds, y):
    summaries = {k: summarize(v) for k, v in metrics.items()}
    mean_order = [m + "__LAST90" for m in cfg["methods"]]
    probability_order = [m + "__" + r for m in cfg["methods"] for r in cfg["rules"]]
    mean_winner = choose(summaries, cfg["selection"]["mean_keys"], mean_order)
    prob_winner = choose(
        summaries, cfg["selection"]["probability_keys"], probability_order
    )
    c, r = summaries[REG + "__LAST90"], summaries[HALF + "__LAST90"]
    tests = {"average_" + k: c[k] <= 0.99 * r[k] for k in ("mae", "rmse")}
    for i, point in enumerate(cfg["points"]):
        for k in ("mae", "rmse"):
            tests[point + "_" + k] = (
                metrics[REG + "__LAST90"][i][k]
                <= metrics[HALF + "__LAST90"][i][k] + 1e-6
            )
    n = cfg["stages"]["development"][0]
    paired = []
    for seed in cfg["seeds"]:
        a = summarize(scores(y, seeds[f"{REG}__{seed}"][n:]))
        b = summarize(scores(y, seeds[f"{HALF}__{seed}"][n:]))
        paired.append(all(a[k] < b[k] for k in ("mae", "rmse")))
    tests["two_paired_seeds"] = sum(paired) >= 2
    tests["bplus_mean_gate"] = effect(
        metrics[REG + "__LAST90"], metrics[B + "__LAST90"], cfg
    )["mean_pass"]
    result = dict(
        time_utc=utc(),
        selection_phase="development",
        mean_winner=mean_winner.split("__")[0],
        probability_winner=prob_winner,
        seed_reg_beats_half=paired,
        search_trigger_checks=tests,
        search_recommendation=all(tests.values()),
        new_training_authorized_in_this_config=False,
        final_labels_used=False,
    )
    write_json(root / "selection.json", result)
    lock(root, "selection_lock.json", [root / "selection.json"], phase="development")
    event(
        root,
        "development_selection_locked",
        mean_winner=result["mean_winner"],
        probability_winner=prob_winner,
    )


def run(phase):
    cfg = spec()
    root = ROOT / cfg["out"]
    check_deadline(cfg)
    guard_sources()
    for p, digest in read_json(root / "implementation_lock.json")["files"].items():
        assert sha(ROOT / p) == digest, p
    assert (
        read_json(root / "implementation_verification/receipt.json")["status"]
        == "passed"
    )
    if phase == "final_exploratory":
        verify_lock(root / "selection_lock.json")
        verify_lock(root / "development/scoring_lock.json")
    dest = root / phase
    if dest.exists():
        raise FileExistsError(
            "Preserve prior attempt; do not overwrite or retry implicitly"
        )
    dest.mkdir(parents=True)
    event(root, "phase_started", phase=phase)
    means, full, seeds, dates = assemble(cfg, phase)
    n, end = cfg["stages"][phase]
    np.savez_compressed(dest / "means.npz", **means)
    np.savez_compressed(dest / "full_means.npz", dates=dates, **full)
    np.savez_compressed(dest / "seed_full_means.npz", **seeds)
    lock(dest, "mean_lock.json", list(dest.glob("*.npz")), phase=phase, rows=end - n)
    event(root, "means_locked", phase=phase)
    source = cfg["calibration"]["source"][phase]
    prior, _, _, _ = assemble(cfg, source["phase"])
    lo, hi = source["indices"]
    prior_start = cfg["stages"][source["phase"]][0]
    # This n-row read serves calibration and unit conversion only; target labels arrive below.
    prefix = label_prefix(cfg, root, n, phase + "_mature_calibration_and_units")
    issue = {"dates": dates[n:], **{m + "__mean": a for m, a in means.items()}}
    history = dict(
        indices=np.arange(lo, hi),
        horizons=np.arange(lo, hi) - source["teacher_prefix"] + 1,
        unit_old=unit(prefix[: source["teacher_prefix"]]),
        unit_new=unit(prefix),
        observed=prefix[lo:hi],
    )
    for method in cfg["methods"]:
        hmean = prior[method][lo - prior_start : hi - prior_start]
        history[method + "__mean"] = hmean
        for rule in cfg["rules"]:
            sigma, indices, errors = calibration(hmean, prefix, source, end - n, rule)
            issue[method + "__" + rule + "__sigma"] = sigma
            issue[rule + "__history_indices"] = indices + lo
            history[method + "__error"] = errors
    np.savez_compressed(dest / "calibration_history.npz", **history)
    np.savez_compressed(dest / "issued_distribution.npz", **issue)
    lock(
        dest,
        "distribution_lock.json",
        [dest / "issued_distribution.npz", dest / "calibration_history.npz"],
        phase=phase,
        latest_observed_index=n - 1,
        source_indices=[lo, hi],
        source_teacher_prefix=source["teacher_prefix"],
        source_phase=source["phase"],
        update_during_forecast=False,
    )
    event(
        root,
        "distribution_locked",
        phase=phase,
        last_calibration_index=hi - 1,
        forecast_start=n,
    )
    target = label_prefix(cfg, root, end, phase + "_scoring_after_lock")[n:]
    metrics = score_phase(cfg, dest, target, means, seeds, issue, dates)
    if phase == "development":
        selection(cfg, root, metrics, seeds, target)
    else:
        selected = read_json(root / "selection.json")
        write_json(
            dest / "frozen_selection_evaluation.json",
            dict(
                selection=selected,
                mean=summarize(metrics[selected["mean_winner"] + "__LAST90"]),
                probability=summarize(metrics[selected["probability_winner"]]),
                selection_changed=False,
            ),
        )
    lock(
        dest,
        "scoring_lock.json",
        [p for p in dest.iterdir() if p.is_file()],
        phase=phase,
        methods=len(cfg["methods"]),
        distributions=18,
        new_fits=0,
        optimizer_updates=0,
    )
    event(root, "phase_completed", phase=phase, distributions=18, new_fits=0)
    print(
        pd.read_csv(dest / "summary.csv")[
            [
                "model",
                "rule",
                "mae",
                "rmse",
                "crps",
                "interval_score90",
                "coverage90",
                "width90",
            ]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["development", "final_exploratory"])
    args = parser.parse_args()
    try:
        run(args.phase)
    except Exception:
        root = ROOT / spec()["out"]
        event(root, "run_error", phase=args.phase, traceback=traceback.format_exc())
        raise
