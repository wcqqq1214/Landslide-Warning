"""One bounded v2.3 development run; fixed checkpoints, explicit stop report."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time
import traceback

import numpy as np
import pandas as pd
import torch

from physics_guided.data import POINTS
from physics_guided.probability import crps, summarize
from physics_guided.reference import ROOT, save_json, sha

from .creep import CreepPinn, memory_states
from .creep_workflow import (
    build_inputs,
    evaluate_distributions,
    forward,
    guard_sources,
    inputs_from_saved,
    loss,
    native_replay,
    prepare_reference,
    read_labels,
    replay_audit,
    tensor,
)

CONFIG = ROOT / "config/ootang_probability_pinn.v2_3.json"
CODE_FILES = [
    "code/physics_guided_pinn/creep.py",
    "code/physics_guided_pinn/creep_workflow.py",
    "code/physics_guided_pinn/run_creep.py",
    "code/physics_guided_pinn/equations.py",
    "code/physics_guided_pinn/trace.py",
    "code/physics_guided_pinn/substep_audit.py",
    "code/physics_guided/reference.py",
    "code/physics_guided/data.py",
    "code/physics_guided/probability.py",
    "code/physics_guided_forecast_error/artifacts.py",
    "code/physics_guided_forecast_error/core.py",
]


def now():
    return datetime.now(timezone.utc).isoformat()


def remaining(deadline):
    return (
        datetime.fromisoformat(deadline) - datetime.now(timezone.utc)
    ).total_seconds()


def setup():
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)


def check_contract(spec):
    required = dict(
        stage="development",
        fit_days=792,
        end_days=1168,
        warmup_days=30,
        seeds=[0, 1, 2],
        updates_per_seed=300,
        max_total_updates=900,
        run_final_stage=False,
        substeps=64,
        device="cpu",
        dtype="float64",
        threads=1,
        optimizer=dict(
            name="Adam",
            lr=0.001,
            betas=[0.9, 0.999],
            eps=1e-8,
            weight_decay=0,
            gradient_norm_limit=1,
        ),
        architecture=dict(
            creep=[4, 8, 1],
            plastic=[9, 16, 16, 1],
            scale=[3, 4],
            parameters_per_seed=514,
        ),
        response_factor=2.0,
        response_regularization=0.001,
        sigma_floor_mm=0.001,
        feature_rms_floor=1e-6,
        plastic_rate_floor_mm_day=1e-6,
        force_rms_floor=1.0,
        slip_rms_floor_mm=1e-8,
    )
    if any(spec.get(k) != v for k, v in required.items()):
        raise ValueError("The bounded development contract has changed")
    guard_sources(spec)


def snapshot(out, spec):
    paths = [*CODE_FILES, str(CONFIG.relative_to(ROOT)), spec["plan"]]
    hashes = {}
    for name in paths:
        source, target = ROOT / name, out / "sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        hashes[name] = sha(source)
    save_json(
        out / "source_snapshot.json",
        dict(
            files=hashes,
            git_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
        ),
    )
    return hashes


def check_snapshot(hashes):
    if any(sha(ROOT / name) != digest for name, digest in hashes.items()):
        raise ValueError("Implementation or configuration changed during the run")


def prepare_run(out, spec):
    saved, trace, labels, library, audit = prepare_reference(spec, out)
    save_json(out / "reference_audit.json", audit)
    if not audit["passed"]:
        raise ArithmeticError(
            "Original full-development trace failed its frozen tolerance gate"
        )
    data = build_inputs(saved, trace, labels, spec["fit_days"])
    save_json(out / "normalizers.json", data.normalizers)
    c = data.coefficients
    beta = (1 / 64) / (c.tau_motion + 1 / 64)
    dp = tensor(trace["lcp"][:, :4]) / beta
    with torch.no_grad():
        reconstructed = memory_states(
            dp,
            tensor(trace["loads"][:, 4:8]),
            tensor(trace["current"][:, 20:]),
            c,
            reference_slip_tolerance=-spec["tolerances"]["native_min_dx"] / beta,
        )[1:]
    difference = np.abs(reconstructed.numpy() - trace["current"])
    rounding = spec["tolerances"]["roundoff_factor"] * np.finfo(float).eps
    floors = np.array([1e-8] * 8 + [1e-7] * 12 + [1e-8] * 4)
    bound = floors + rounding * (abs(reconstructed.numpy()) + abs(trace["current"]))
    recurrence = dict(
        passed=bool((difference <= bound).all()),
        max_absolute_by_state_block=[
            float(x.max()) for x in np.split(difference, 6, axis=1)
        ],
        max_error_to_bound=float((difference / bound).max()),
        days=len(saved["mean"]),
        substeps=len(dp),
        reference_negative_dx_count=int((trace["lcp"][:, :4] < 0).sum()),
        reference_negative_values_preserved=True,
        neural_increments_remain_strictly_nonnegative=True,
    )
    save_json(out / "recurrence_audit.json", recurrence)
    if not recurrence["passed"]:
        raise ArithmeticError(
            "Full-history tensor recurrence differs from the original C trace"
        )
    torch.manual_seed(0)
    model = CreepPinn(data.normalizers["displacement_scale"])
    result = forward(model, data)
    total, terms = loss(result, data)
    total.backward()
    gradients = {
        name: float(p.grad.norm())
        for name, p in model.named_parameters()
        if p.grad is not None
    }
    if (
        not torch.isfinite(total)
        or len(gradients) != len(list(model.named_parameters()))
        or not all(np.isfinite(list(gradients.values())))
    ):
        raise ArithmeticError("Initial full-history loss/gradient is invalid")
    bg_error = float(
        np.max(np.abs(result["background"].detach().numpy() - saved["background"]))
    )
    if bg_error > 1e-8:
        raise ArithmeticError(
            "Zero neural creep response does not reproduce B+ background"
        )
    save_json(
        out / "initial_gradient_audit.json",
        dict(
            passed=True,
            parameters=sum(p.numel() for p in model.parameters()),
            gradients=gradients,
            loss=float(total.detach()),
            terms={k: float(v.detach()) for k, v in terms.items()},
            background_max_difference_mm=bg_error,
            optimizer_updates=0,
            note="Some hidden-layer gradients start at zero because the last layer is zero initialized; no efficacy claim.",
        ),
    )
    np.savez_compressed(
        out / "fit_labels.npz", observed=labels, dates=saved["dates"][: len(labels)]
    )
    return saved, data, library


def export_daily(out, spec, saved, observed, distributions):
    frames = []
    for name, (means, sigmas) in distributions.items():
        n = spec["end_days"]
        mean = means.mean(axis=0)
        row = dict(
            model=name,
            date=np.repeat(saved["dates"], 4),
            station=np.tile(POINTS, n),
            phase=np.repeat(
                np.where(np.arange(n) < spec["fit_days"], "train", "prediction"), 4
            ),
            valid=np.repeat(np.arange(n) >= 30, 4),
            observed_mm=observed.ravel(),
            mean_mm=mean.ravel(),
            error_mm=(mean - observed).ravel(),
        )
        if sigmas is not None:
            # Frozen v1.1 warmup means contain NaNs; probability scoring starts at day 30.
            summary = summarize(means[:, 30:], sigmas[:, 30:])
            for key, value in summary.items():
                if key != "mean":
                    row[key + "_mm"] = np.vstack(
                        (np.full((30, 4), np.nan), value)
                    ).ravel()
            row["crps_mm"] = np.vstack(
                (
                    np.full((30, 4), np.nan),
                    crps(means[:, 30:], sigmas[:, 30:], observed[30:]),
                )
            ).ravel()
        frames.append(pd.DataFrame(row))
    pd.concat(frames, ignore_index=True).to_csv(
        out / "daily_predictions.csv", index=False
    )


def plot_forecast(out, spec, saved, observed, means, sigmas):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    h = spec["fit_days"]
    dates = pd.to_datetime(saved["dates"][h:])
    q = summarize(means[:, h:], sigmas[:, h:])
    fig, axes = plt.subplots(2, 2, figsize=(12, 7), layout="constrained")
    for j, ax in enumerate(axes.flat):
        ax.plot(dates, observed[h:, j], color="black", lw=1.6, label="Observed")
        ax.plot(dates, saved["mean"][h:, j], color="#cf8122", lw=1.5, label="B+")
        ax.plot(dates, q["mean"][:, j], color="#1769aa", lw=1.4, label="PINN (primary)")
        ax.fill_between(
            dates,
            q["lower_90"][:, j],
            q["upper_90"][:, j],
            color="#1769aa",
            alpha=0.18,
            label="PINN 90% interval",
        )
        ax.set(title=POINTS[j], ylabel="Cumulative displacement / mm")
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.grid(alpha=0.2)
    axes[0, 0].legend(fontsize=8)
    fig.suptitle("v2.3 development: all 376 prediction days; no tail exclusion")
    fig.savefig(out / "development_forecast.png", dpi=160)
    plt.close(fig)


def score_saved(out, spec, saved, means, sigmas, reference_means):
    # This is the first access to the held-out development observation labels.
    if not (out / "prediction_lock.json").exists():
        raise ValueError("Predictions must be locked before reading development labels")
    observed = read_labels(ROOT / spec["input"], spec["end_days"])
    old_file = ROOT / spec["source_run"] / "development/M1/selected_predictions.npz"
    with np.load(old_file, allow_pickle=False) as old:
        old_m, old_s = old["means"].copy(), old["sigmas"].copy()
    records, table, acceptance = evaluate_distributions(
        spec, observed, saved["mean"], old_m, old_s, means, sigmas, reference_means
    )
    table.to_csv(out / "metrics.csv", index=False)
    save_json(out / "metrics.json", records)
    save_json(out / "acceptance.json", acceptance)
    export_daily(
        out,
        spec,
        saved,
        observed,
        {
            "M0": (saved["mean"][None], None),
            "v1.1-e0": (old_m, old_s),
            "PINN": (means, sigmas),
            "replay_diagnostic": (reference_means, sigmas),
        },
    )
    plot_forecast(out, spec, saved, observed, means, sigmas)
    return acceptance


def execute(out, spec):
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    execution = dict(
        started_at=now(),
        deadline=spec["deadline_utc"],
        status="running",
        updates_by_seed={},
        native_calls=0,
        baseline_forward_calls=0,
        final_stage_started=False,
        optimizer_updates=0,
        scoring_started=False,
        environment=dict(
            python=sys.version,
            numpy=np.__version__,
            torch=torch.__version__,
            platform=platform.platform(),
            threads=1,
            pid=os.getpid(),
        ),
    )
    hashes = snapshot(out, spec)
    save_json(out / "execution.json", execution)
    try:
        if remaining(spec["training_deadline_utc"]) <= 0:
            raise TimeoutError("The fixed training deadline has already expired")
        print(json.dumps(dict(event="preparation", at=now())), flush=True)
        execution["baseline_forward_calls"] = 1
        execution["native_calls"] = 1
        saved, data, library = prepare_run(out, spec)
        print(
            json.dumps(
                dict(
                    event="preflight_passed",
                    at=now(),
                    remaining_seconds=remaining(spec["training_deadline_utc"]),
                )
            ),
            flush=True,
        )
        means, sigmas, reference_means = [], [], []
        reloads = {}
        for seed in spec["seeds"]:
            if remaining(spec["training_deadline_utc"]) <= 0:
                raise TimeoutError("Fixed training deadline reached before a seed")
            torch.manual_seed(seed)
            model = CreepPinn(data.normalizers["displacement_scale"])
            optimizer = torch.optim.Adam(
                model.parameters(),
                lr=0.001,
                betas=(0.9, 0.999),
                eps=1e-8,
                weight_decay=0,
            )
            directory = out / f"seed_{seed}"
            directory.mkdir()
            execution["updates_by_seed"][str(seed)] = 0
            seed_start = time.monotonic()
            with (directory / "training.jsonl").open("x") as stream:
                for step in range(1, spec["updates_per_seed"] + 1):
                    if remaining(spec["training_deadline_utc"]) <= 0:
                        raise TimeoutError(
                            "Fixed training deadline reached; no earlier checkpoint is selected"
                        )
                    optimizer.zero_grad(set_to_none=True)
                    result = forward(model, data)
                    total, terms = loss(result, data)
                    if not torch.isfinite(total):
                        raise ArithmeticError("Nonfinite training loss")
                    total.backward()
                    norm = torch.nn.utils.clip_grad_norm_(
                        model.parameters(), 1, error_if_nonfinite=True
                    )
                    optimizer.step()
                    execution["updates_by_seed"][str(seed)] = step
                    execution["optimizer_updates"] += 1
                    row = dict(
                        seed=seed,
                        update=step,
                        at=now(),
                        elapsed_seconds=time.monotonic() - seed_start,
                        loss_before_update=float(total.detach()),
                        gradient_norm_before_clip=float(norm),
                        terms={k: float(v.detach()) for k, v in terms.items()},
                    )
                    stream.write(json.dumps(row, allow_nan=False) + "\n")
                    if step == 1 or step % 25 == 0:
                        stream.flush()
                        print(json.dumps(row), flush=True)
                        save_json(out / "execution.json", execution)
                    del result, total, terms
            with torch.no_grad():
                result = forward(model, data)
            center = result["center"].detach().clone()
            torch.save(
                dict(
                    model=model.state_dict(),
                    center=center,
                    epoch=300,
                    seed=seed,
                    normalizers=data.normalizers,
                ),
                directory / "e300.pt",
            )
            arrays = {
                k: result[k].numpy()
                for k in ("mean", "sigma", "states", "background", "a", "center")
            }
            np.savez_compressed(directory / "prediction.npz", **arrays)
            raw = {
                k: dict(
                    max_absolute=float(v.abs().max()),
                    minimum=float(v.min()),
                    rms=float(v.square().mean().sqrt()),
                )
                for k, v in result["raw"].items()
            }
            save_json(directory / "neural_residuals.json", raw)
            replay_inputs = inputs_from_saved(saved, saved["length"])
            replay_inputs["background"] = arrays["background"]
            execution["native_calls"] += 1
            replay = native_replay(library, replay_inputs)
            audit = replay_audit(replay, saved, arrays["background"], spec)
            np.savez_compressed(directory / "reference_trace.npz", **replay)
            save_json(directory / "reference_audit.json", audit)
            if not audit["passed"]:
                raise ArithmeticError(
                    "Same-background original C replay failed its physical gate"
                )
            ref_mean = (
                replay["coordinates"] @ saved["observation_matrix"].T + saved["y0"]
            )
            means.append(arrays["mean"])
            sigmas.append(arrays["sigma"])
            reference_means.append(ref_mean)
            loaded = torch.load(
                directory / "e300.pt", map_location="cpu", weights_only=True
            )
            restored = CreepPinn(data.normalizers["displacement_scale"])
            restored.load_state_dict(loaded["model"], strict=True)
            with torch.no_grad():
                again = forward(restored, data, center=loaded["center"])
            reloads[str(seed)] = {
                k: float(np.abs(again[k].numpy() - arrays[k]).max())
                for k in ("mean", "sigma", "background", "states")
            }
            if any(v > 1e-9 for v in reloads[str(seed)].values()):
                raise ArithmeticError(
                    "Saved model/center reload changed the primary prediction"
                )
            print(
                json.dumps(
                    dict(
                        event="seed_complete",
                        seed=seed,
                        at=now(),
                        seconds=time.monotonic() - seed_start,
                    )
                ),
                flush=True,
            )
            del result, arrays, replay, again, model, optimizer, restored
        check_snapshot(hashes)
        guard_sources(spec)
        means, sigmas, reference_means = (
            np.stack(means),
            np.stack(sigmas),
            np.stack(reference_means),
        )
        np.savez_compressed(
            out / "selected_predictions.npz",
            means=means,
            sigmas=sigmas,
            reference_means=reference_means,
            dates=saved["dates"],
        )
        save_json(
            out / "reload_verification.json", dict(passed=True, differences=reloads)
        )
        save_json(
            out / "prediction_lock.json",
            dict(
                locked_at=now(),
                epochs=[300, 300, 300],
                seeds=spec["seeds"],
                predictions_sha256=sha(out / "selected_predictions.npz"),
                normalizers_sha256=sha(out / "normalizers.json"),
                checkpoint_sha256={
                    str(seed): sha(out / f"seed_{seed}/e300.pt")
                    for seed in spec["seeds"]
                },
                labels_used_for_selection=False,
                final_stage_started=False,
            ),
        )
        execution["scoring_started"] = True
        acceptance = score_saved(out, spec, saved, means, sigmas, reference_means)
        execution.update(
            status="completed",
            effectiveness_passed=acceptance["passed"],
            stop_reason="development report completed; no automatic final-stage training",
        )
    except Exception as error:
        execution.update(
            status="stopped", stop_reason=f"{type(error).__name__}: {error}"
        )
        (out / "error.txt").write_text(traceback.format_exc())
        print(
            json.dumps(dict(event="stopped", reason=execution["stop_reason"])),
            flush=True,
        )
    finally:
        execution.update(
            finished_at=now(),
            elapsed_seconds=time.monotonic() - started,
            maximum_rss_native_units=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            total_window_seconds_remaining=remaining(spec["deadline_utc"]),
        )
        save_json(out / "execution.json", execution)
        save_json(
            out / "artifact_manifest.json",
            dict(
                files={
                    str(p.relative_to(out)): dict(sha256=sha(p), bytes=p.stat().st_size)
                    for p in sorted(out.rglob("*"))
                    if p.is_file() and p.name != "artifact_manifest.json"
                }
            ),
        )
    return execution


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    spec = json.loads(CONFIG.read_text())
    check_contract(spec)
    setup()
    result = execute(args.output.resolve(), spec)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
