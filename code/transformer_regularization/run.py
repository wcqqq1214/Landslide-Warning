"""Nine fixed fits, issue-before-score locks, complete held-out evaluation."""

import argparse
import json
import time
import traceback

import numpy as np
import pandas as pd
import torch

from sequence_conditional.run import write_scores
from .core import (
    CONFIG,
    NEW,
    OLD,
    ROOT,
    TrajectoryModel,
    array_sha,
    calibrate,
    check_deadline,
    choose,
    event,
    gates,
    guard_sources,
    labels,
    load_npz,
    lock,
    objective,
    predict,
    read_json,
    scores,
    sha,
    spec,
    training_inputs,
    utc,
    verify_lock,
    write_json,
)


def train_one(cfg, root, phase, seed, data, y):
    dest = root / phase / NEW / f"seed_{seed}"
    if (dest / "complete.json").exists():
        return verify_lock(dest / "complete.json")
    dest.mkdir(parents=True, exist_ok=False)
    n, steps = len(y), cfg["regularization"]["fixed_updates"]
    strength = cfg["regularization"]["lambda"]
    scale, x, target = training_inputs(data, y, cfg)
    model = TrajectoryModel(cfg, seed, OLD)
    original = torch.load(
        ROOT / cfg["reuse_sequence"] / phase / OLD / f"seed_{seed}/e0.pt",
        weights_only=True,
        map_location="cpu",
    )
    for k, v in model.state_dict().items():
        torch.testing.assert_close(v, original["state_dict"][k], rtol=0, atol=0)
    nc = cfg["neural"]
    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=nc["lr"],
        betas=tuple(nc["betas"]),
        eps=nc["eps"],
        weight_decay=0,
    )
    started = time.monotonic()
    event(root, "fit_started", phase=phase, arm=NEW, seed=seed, rows=n, updates=steps)
    with (dest / "training.jsonl").open("w") as log:
        for step in range(steps + 1):
            check_deadline(cfg)
            if step:
                model.train()
                optimizer.zero_grad(set_to_none=True)
                total, loss, penalty = objective(model(x), target, strength)
                total.backward()
                grad = float(
                    sum(
                        p.grad.square().sum()
                        for p in model.parameters()
                        if p.grad is not None
                    ).sqrt()
                )
                if not np.isfinite(grad):
                    raise ArithmeticError("Nonfinite gradient")
                optimizer.step()
                log.write(
                    json.dumps(
                        dict(
                            step=step,
                            pre_update_loss=float(loss.detach()),
                            pre_update_penalty=float(penalty.detach()),
                            pre_update_total=float(total.detach()),
                            grad_norm=grad,
                        ),
                        allow_nan=False,
                    )
                    + "\n"
                )
                log.flush()
            if step in [0] + nc["checkpoints"]:
                mean = predict(model, scale, OLD, data["x"], data["mean"])
                q = (mean[:n] - data["mean"][:n]) / scale.unit
                error = (mean[:n] - y) / scale.unit
                saved = dict(
                    state_dict=model.state_dict(),
                    scaling=scale.state,
                    seed=seed,
                    arm=OLD,
                    candidate=NEW,
                    family="TRANSFORMER",
                    step=step,
                    training_prefix=n,
                    regularization_lambda=strength,
                    config_sha256=sha(CONFIG),
                )
                torch.save(saved, dest / f"e{step}.pt")
                np.save(dest / f"e{step}_mean.npy", mean)
                write_json(
                    dest / f"e{step}_fit.json",
                    dict(
                        step=step,
                        normalized_data_loss=float(np.mean(error**2)),
                        normalized_penalty=float(np.mean(q**2)),
                        normalized_total=float(
                            np.mean(error**2) + strength * np.mean(q**2)
                        ),
                        correction_rms_mm=np.sqrt(
                            np.mean((mean[:n] - data["mean"][:n]) ** 2, 0)
                        ).tolist(),
                        metrics=scores(y, mean[:n]),
                        mean_sha256=array_sha(mean),
                    ),
                )
                print(
                    f"{phase} seed={seed} update={step} data_mse={np.mean(error**2):.8f} "
                    f"penalty={np.mean(q**2):.8f}",
                    flush=True,
                )
    result = lock(
        dest,
        "complete.json",
        list(dest.glob("*")),
        phase=phase,
        arm=NEW,
        seed=seed,
        updates=steps,
        rows=n,
        status="fit_complete",
        elapsed_seconds=time.monotonic() - started,
    )
    event(
        root,
        "fit_completed",
        phase=phase,
        seed=seed,
        updates=steps,
        elapsed_seconds=result["elapsed_seconds"],
    )
    return result


def run(phase):
    cfg = spec()
    root = ROOT / cfg["out"]
    check_deadline(cfg)
    guard_sources()
    for p, h in read_json(root / "implementation_lock.json")["files"].items():
        if sha(ROOT / p) != h:
            raise ValueError("Training implementation changed: " + p)
    assert (
        read_json(root / "implementation_verification/receipt.json")["status"]
        == "passed"
    )
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    dest = root / phase
    if (dest / "scoring_lock.json").exists():
        verify_lock(dest / "scoring_lock.json")
        print(phase + " already complete; no new fits")
        return
    dest.mkdir(parents=True, exist_ok=True)
    n, end = cfg["stages"][phase]
    previous = "internal" if phase == "development" else "development"
    if phase != "internal":
        verify_lock(root / previous / "scoring_lock.json")
        cal = read_json(root / previous / "next_calibration.json")
        assert cal["next_origin"] == n and cal["last_label_index"] < n
    if phase == "final_exploratory":
        selection = read_json(root / "selection.json")
        assert selection["source_phase"] == "development"
    event(root, "phase_started", phase=phase, training_rows=n, prediction_rows=end - n)
    data = load_npz(
        ROOT / cfg["reuse"] / f"implementation_verification/teacher_{n}.npz"
    )
    assert data["x"].shape == (end, 22) and data["mean"].shape == (end, 4)
    y = labels(cfg, root, n, phase + "_training")
    scale, _, _ = training_inputs(data, y, cfg)
    olddir = ROOT / cfg["reuse_sequence"] / phase
    assert scale.state == read_json(olddir / "scaling.json")
    write_json(dest / "scaling.json", scale.state)
    oldmeans = load_npz(olddir / "ensemble_selected.npz")
    basic = load_npz(olddir / "reused_predictions.npz")
    means = {
        m: (oldmeans[OLD][n:] if m == OLD else basic[m]) for m in cfg["reuse_methods"]
    }
    np.savez_compressed(dest / "reused_predictions.npz", **means)
    event(
        root, "controls_reused", phase=phase, methods=cfg["reuse_methods"], new_fits=0
    )
    fits = [train_one(cfg, root, phase, s, data, y) for s in cfg["neural"]["seeds"]]
    ensemble = np.mean(
        [
            np.load(dest / NEW / f"seed_{s}/e400_mean.npy")
            for s in cfg["neural"]["seeds"]
        ],
        axis=0,
    )
    np.savez_compressed(dest / "ensemble_selected.npz", **{NEW: ensemble})
    means[NEW] = ensemble[n:]
    lock(
        dest,
        "mean_lock.json",
        list(dest.glob("**/*.pt"))
        + list(dest.glob("**/*.npy"))
        + list(dest.glob("*.npz"))
        + [dest / "scaling.json"],
        phase=phase,
        forecast_start=n,
        forecast_end=end,
        latest_training_label_index=n - 1,
        displacement_feedback=False,
    )
    event(
        root, "all_means_locked", phase=phase, lock_sha256=sha(dest / "mean_lock.json")
    )
    if phase == "internal":
        first = labels(cfg, root, 702, "internal_fixed_version_diagnostic")
        pd.DataFrame(
            [
                dict(model=m, **r)
                for m in cfg["methods"]
                for r in scores(first[n:], means[m][:90])
            ]
        ).to_csv(dest / "first90_diagnostic.csv", index=False, float_format="%.17g")
        observed = labels(cfg, root, end, "internal_mature_calibration")
        pd.DataFrame(
            [
                dict(model=m, **r)
                for m in cfg["methods"]
                for r in scores(observed[n:], means[m])
            ]
        ).to_csv(dest / "metrics_by_point.csv", index=False, float_format="%.17g")
        next_scales = {
            m: calibrate(observed[702:792] - means[m][-90:], cfg).tolist()
            for m in cfg["methods"]
        }
        oldcal = read_json(olddir / "next_calibration.json")["scales"]
        for m in cfg["reuse_methods"]:
            np.testing.assert_array_equal(next_scales[m], oldcal[m])
        write_json(
            dest / "next_calibration.json",
            dict(
                time_utc=utc(),
                next_origin=792,
                error_indices=[702, 792],
                last_label_index=791,
                scales=next_scales,
                source_mean_lock_sha256=sha(dest / "mean_lock.json"),
            ),
        )
        lock(
            dest,
            "scoring_lock.json",
            list(dest.glob("*.csv")) + [dest / "next_calibration.json"],
            status="complete",
            phase=phase,
            neural_fits=len(fits),
            optimizer_updates=1200,
        )
        event(root, "phase_completed", phase=phase)
        return
    sigmas = {m: np.array(cal["scales"][m]) for m in cfg["methods"]}
    np.savez_compressed(
        dest / "issued_distribution.npz",
        dates=data["dates"][n:],
        **{m + "__mean": mu for m, mu in means.items()},
        **{m + "__sigma": sd for m, sd in sigmas.items()},
    )
    lock(
        dest,
        "distribution_lock.json",
        [dest / "issued_distribution.npz"],
        phase=phase,
        forecast_start=n,
        forecast_end=end,
        latest_observed_index=n - 1,
        calibration_indices=cal["error_indices"],
        calibration_source_sha256=sha(root / previous / "next_calibration.json"),
    )
    event(
        root,
        "distribution_locked",
        phase=phase,
        lock_sha256=sha(dest / "distribution_lock.json"),
    )
    observed = labels(cfg, root, end, phase + "_scoring_after_distribution_lock")
    fitting = {
        "BPLUS_CONTINUOUS": data["mean"][:n],
        OLD: oldmeans[OLD][:n],
        NEW: ensemble[:n],
    }
    metrics, summary = write_scores(
        dest, phase, observed[n:], means, sigmas, y, fitting, data["dates"][n:], cfg
    )
    seed_rows = []
    for m in (OLD, NEW):
        for s in cfg["neural"]["seeds"]:
            directory = olddir if m == OLD else dest
            mu = np.load(directory / m / f"seed_{s}/e400_mean.npy")[n:]
            seed_rows.extend(
                dict(model=m, seed=s, **r) for r in scores(observed[n:], mu, sigmas[m])
            )
    pd.DataFrame(seed_rows).to_csv(
        dest / "seed_metrics.csv", index=False, float_format="%.17g"
    )
    if phase == "development":
        decision = dict(
            time_utc=utc(),
            source_phase=phase,
            fixed_updates=400,
            regularization_lambda=1,
            mean_winner=choose(
                summary, cfg["selection"]["mean_keys"], cfg["methods"], 1e-12
            ),
            probability_winner=choose(
                summary, cfg["selection"]["probability_keys"], cfg["methods"], 1e-12
            ),
            effects=gates(metrics, cfg),
            complete_final_even_if_failed=True,
        )
        write_json(root / "selection.json", decision)
        event(
            root,
            "development_selection_locked",
            selection_sha256=sha(root / "selection.json"),
        )
        next_scales = {
            m: calibrate(observed[1078:1168] - means[m][-90:], cfg).tolist()
            for m in cfg["methods"]
        }
        oldcal = read_json(olddir / "next_calibration.json")["scales"]
        for m in cfg["reuse_methods"]:
            np.testing.assert_array_equal(next_scales[m], oldcal[m])
        write_json(
            dest / "next_calibration.json",
            dict(
                time_utc=utc(),
                next_origin=1168,
                error_indices=[1078, 1168],
                last_label_index=1167,
                scales=next_scales,
                source_distribution_lock_sha256=sha(dest / "distribution_lock.json"),
            ),
        )
    else:
        write_json(
            dest / "frozen_selection_evaluation.json",
            dict(
                selection=selection,
                final_scores_of_frozen_winners={
                    k: summary[selection[k]]
                    for k in ("mean_winner", "probability_winner")
                },
                all_method_summary=summary,
                exploratory=True,
            ),
        )
    paths = list(dest.glob("*.csv")) + [dest / "effect_gates.json"]
    paths += [
        p
        for p in (
            dest / "next_calibration.json",
            dest / "frozen_selection_evaluation.json",
        )
        if p.exists()
    ]
    lock(
        dest,
        "scoring_lock.json",
        paths,
        status="complete",
        phase=phase,
        neural_fits=len(fits),
        optimizer_updates=1200,
    )
    event(
        root,
        "phase_completed",
        phase=phase,
        rmse={m: v["rmse"] for m, v in summary.items()},
    )
    print(phase, {m: round(v["rmse"], 6) for m, v in summary.items()}, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=tuple(spec()["stages"]))
    args = parser.parse_args()
    try:
        run(args.phase)
    except Exception:
        root = ROOT / spec()["out"]
        event(
            root, "execution_error", phase=args.phase, traceback=traceback.format_exc()
        )
        raise
