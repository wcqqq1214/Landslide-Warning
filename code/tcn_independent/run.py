"""Independent forecast experiment phases. Scoring alone loads the final labels."""

import argparse
import json
import subprocess
import time

import numpy as np
import pandas as pd
from scipy.special import ndtr, ndtri
import torch

from short_horizon.common import ROOT, CALLS, sha, save_json, now
from short_horizon.data import observations, load_cache, query
from short_horizon.physics import PhysicalBank
from tcn_short_horizon.models import predict
from .engine import read_spec, guard, Learners, forecast_origin, calibrate


def arrays(path):
    with np.load(path) as a:
        return {key: a[key].copy() for key in a.files}


def lock(directory):
    save_json(
        directory / "artifact_manifest.json",
        {
            str(p.relative_to(directory)): sha(p)
            for p in sorted(directory.rglob("*"))
            if p.is_file() and p.name != "artifact_manifest.json"
        },
    )


def check_lock(directory):
    manifest = json.loads((directory / "artifact_manifest.json").read_text())
    for name, digest in manifest.items():
        if sha(directory / name) != digest:
            raise ValueError("Changed locked artifact: " + str(directory / name))


def event(out, kind, **details):
    entry = dict(time_utc=now(), event=kind, **details)
    with (out / "events.jsonl").open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n")
    print(json.dumps(entry, ensure_ascii=False), flush=True)


def calibration_phase(spec, out):
    # No final-period displacement or forcing is loaded by this phase.
    y, forcing, _ = observations(spec, spec["train_end"])
    bank = PhysicalBank(forcing, y[0], [612, 792])
    learners = Learners(spec)
    origins = np.arange(spec["calibration"]["origin_first"], spec["train_end"])
    length = spec["forecast_length"]
    lengths = np.minimum(length, spec["train_end"] - origins)
    means = {n: np.full((len(origins), length, 4), np.nan) for n in spec["models"]}
    seeds = {
        n: np.full((len(origins), 3, length, 4), np.nan)
        for n in ("TCN_DIRECT", "TCN_BRES")
    }
    audits = []
    for i, (origin, count) in enumerate(zip(origins, lengths)):
        mu, seed_mu, audit = forecast_origin(
            spec, y, bank, learners, int(origin), int(count)
        )
        for name in means:
            means[name][i, :count] = mu[name]
        for name in seeds:
            seeds[name][i, :, :count] = seed_mu[name]
        audit["nonfinite_output_cells"] = {
            n: int((~np.isfinite(mu[n])).sum()) for n in mu
        }
        audits.append(audit)
        if i % 20 == 0 or i == len(origins) - 1:
            event(
                out,
                "calibration_progress",
                completed=i + 1,
                total=len(origins),
                origin=int(origin),
                failures=audit["nonfinite_output_cells"],
            )
    sigma, pools = calibrate(
        origins, means, y, spec["train_end"], length, spec["calibration"]["window"]
    )
    np.savez_compressed(
        out / "forecasts.npz", origins=origins, lengths=lengths, **means
    )
    np.savez_compressed(out / "seed_forecasts.npz", **seeds)
    np.savez_compressed(out / "scales.npz", pool_origins=pools, **sigma)
    save_json(out / "origin_audits.json", audits)
    save_json(
        out / "receipt.json",
        dict(
            forecast_origins=len(origins),
            forecast_days=int(lengths.sum()),
            observed_prefix=len(y),
            per_lead_pool_size=90,
            targets_before=spec["train_end"],
            calls=CALLS.copy(),
            maximum_historical_state_difference=max(
                a["historical_state_difference"] for a in audits
            ),
            new_fits=0,
            optimizer_updates=0,
            nonfinite_scale_cells={
                n: int((~np.isfinite(sigma[n])).sum()) for n in sigma
            },
        ),
    )


def forecast_phase(spec, root, out):
    check_lock(root / "calibration")
    y, forcing, dates = observations(spec, spec["train_end"])
    bank = PhysicalBank(forcing, y[0], spec["teacher_prefixes"])
    learners = Learners(spec)
    mu, seeds, audit = forecast_origin(
        spec, y, bank, learners, len(y), spec["forecast_length"], record=True
    )
    scales = arrays(root / "calibration/scales.npz")
    np.savez_compressed(out / "means.npz", **mu)
    np.savez_compressed(out / "seed_means.npz", **seeds)
    np.savez_compressed(out / "sigma.npz", **{n: scales[n] for n in spec["models"]})
    save_json(out / "origin_audit.json", audit)
    event(
        out,
        "independent_distribution_locked",
        origin=len(y),
        forecast_days=spec["forecast_length"],
        means_sha256=sha(out / "means.npz"),
        sigma_sha256=sha(out / "sigma.npz"),
        final_labels_loaded=False,
    )
    # Training-period one-day reconstruction; inputs remain in the original training prefix.
    oldspec = json.loads((ROOT / spec["tcn_config"]).read_text())
    cache = load_cache(oldspec)
    origins = np.arange(252, len(y))
    data = query(cache, origins)
    reconstruction = dict(
        B_ANCHOR=data["anchor"][:, 0],
        DRIFT1=data["drift"][:, 0],
        RR_DIRECT=learners.ridge[1168].predict(data)[:, 0],
    )
    seed_reconstruction = {}
    for name in ("TCN_DIRECT", "TCN_BRES"):
        group, scaling = learners.groups[1168][name]
        seed_reconstruction[name] = predict(group, name, scaling, data)[:, :, 0]
        reconstruction[name] = seed_reconstruction[name].mean(0)
    np.savez_compressed(
        out / "training_reconstruction.npz", origins=origins, **reconstruction
    )
    np.savez_compressed(out / "training_seed_reconstruction.npz", **seed_reconstruction)
    save_json(
        out / "receipt.json",
        dict(
            origin=len(y),
            last_observed_date=str(dates[-1]),
            forecast_length=spec["forecast_length"],
            observed_prefix_parsed=len(y),
            final_labels_loaded=False,
            new_fits=0,
            optimizer_updates=0,
            calls=CALLS.copy(),
            nonfinite_mean_cells={n: int((~np.isfinite(mu[n])).sum()) for n in mu},
            reconstruction_origins=[int(origins[0]), int(origins[-1])],
            reconstruction_meaning="training_prefix_h1_reconstruction_not_independent_validation",
        ),
    )


def scores(y, mean, sigma=None):
    error = y - mean
    row = dict(
        n=len(y),
        mean_complete=bool(np.isfinite(mean).all()),
        mae=float(np.mean(np.abs(error))),
        rmse=float(np.sqrt(np.mean(error**2))),
    )
    if sigma is None:
        return row
    row["probability_complete"] = bool(
        np.isfinite(mean).all() and np.isfinite(sigma).all()
    )
    z = error / sigma
    crps = error * (2 * ndtr(z) - 1) + sigma * (
        2 * np.exp(-z * z / 2) / np.sqrt(2 * np.pi) - 1 / np.sqrt(np.pi)
    )
    row["crps"] = float(np.mean(crps))
    for level in (80, 90, 95):
        q = ndtri((1 + level / 100) / 2)
        lo, hi = mean - q * sigma, mean + q * sigma
        covered = (y >= lo) & (y <= hi)
        penalty = np.maximum(lo - y, 0) + np.maximum(y - hi, 0)
        row[f"coverage{level}"] = (
            float(covered.mean()) if row["probability_complete"] else float("nan")
        )
        row[f"width{level}"] = float(np.mean(hi - lo))
        row[f"interval_score{level}"] = float(
            np.mean(hi - lo + 2 * penalty / (1 - level / 100))
        )
    return row


def score_phase(spec, root, out):
    check_lock(root / "forecast")
    event(
        out,
        "final_labels_released_after_distribution_lock",
        forecast_manifest_sha256=sha(root / "forecast/artifact_manifest.json"),
    )
    y, _, dates = observations(spec, spec["total_days"])
    means = arrays(root / "forecast/means.npz")
    sigma = arrays(root / "forecast/sigma.npz")
    seeds = arrays(root / "forecast/seed_means.npz")
    reconstruction = arrays(root / "forecast/training_reconstruction.npz")
    start, length = spec["train_end"], spec["forecast_length"]
    metrics, train_metrics, curves, train_curves, seed_metrics = [], [], [], [], []
    for name in spec["models"]:
        for p, point in enumerate(spec["points"]):
            metrics.append(
                dict(
                    model=name,
                    point=point,
                    **scores(y[start:, p], means[name][:, p], sigma[name][:, p]),
                )
            )
            ids = reconstruction["origins"]
            train_metrics.append(
                dict(
                    model=name,
                    point=point,
                    **scores(y[ids, p], reconstruction[name][:, p]),
                )
            )
            row = dict(
                model=name,
                point=point,
                date=dates[start:],
                lead_day=np.arange(1, length + 1),
                observed_mm=y[start:, p],
                mean_mm=means[name][:, p],
                sigma_mm=sigma[name][:, p],
                display_offset_mm=y[0, p],
                observed_zeroed_mm=y[start:, p] - y[0, p],
                mean_zeroed_mm=means[name][:, p] - y[0, p],
            )
            for level in (80, 90, 95):
                q = ndtri((1 + level / 100) / 2)
                row[f"lower{level}_mm"] = means[name][:, p] - q * sigma[name][:, p]
                row[f"upper{level}_mm"] = means[name][:, p] + q * sigma[name][:, p]
            curves.append(pd.DataFrame(row))
            train_curves.append(
                pd.DataFrame(
                    dict(
                        model=name,
                        point=point,
                        date=dates[ids],
                        origin=ids,
                        observed_mm=y[ids, p],
                        mean_mm=reconstruction[name][:, p],
                        display_offset_mm=y[0, p],
                    )
                )
            )
            if name in ("TCN_DIRECT", "TCN_BRES"):
                for seed in spec["seeds"]:
                    seed_metrics.append(
                        dict(
                            model=name,
                            point=point,
                            seed=seed,
                            **scores(y[start:, p], seeds[name][seed, :, p]),
                        )
                    )
    metrics = pd.DataFrame(metrics)
    summary = []
    excluded = {"model", "point", "n", "mean_complete", "probability_complete"}
    for name in spec["models"]:
        rows = metrics[metrics.model == name]
        record = dict(
            model=name,
            n_per_point=length,
            mean_complete=bool(rows.mean_complete.all()),
            probability_complete=bool(rows.probability_complete.all()),
        )
        record.update(
            {
                k: rows[k].mean(skipna=False)
                for k in metrics.columns
                if k not in excluded
            }
        )
        record["pooled_rmse"] = np.sqrt(np.mean((y[start:] - means[name]) ** 2))
        summary.append(record)
    for filename, frame in [
        ("metrics_by_point.csv", metrics),
        ("summary.csv", pd.DataFrame(summary)),
        ("training_metrics.csv", pd.DataFrame(train_metrics)),
        ("forecasts.csv", pd.concat(curves)),
        ("training_reconstruction.csv", pd.concat(train_curves)),
        ("seed_metrics.csv", pd.DataFrame(seed_metrics)),
    ]:
        frame.to_csv(out / filename, index=False, float_format="%.15g")
    pd.DataFrame(
        dict(
            date=np.tile(dates, 4),
            point=np.repeat(spec["points"], len(y)),
            observed_mm=y.T.flatten(),
            display_offset_mm=np.repeat(y[0], len(y)),
        )
    ).to_csv(out / "observations.csv", index=False, float_format="%.15g")
    save_json(
        out / "receipt.json",
        dict(
            final_label_first=start,
            final_label_last=len(y) - 1,
            forecast_days=length,
            point_metric_rows=len(metrics),
            forecast_rows=5 * 4 * length,
            training_curve_rows=5 * 4 * len(reconstruction["origins"]),
            new_fits=0,
            optimizer_updates=0,
        ),
    )
    print(pd.DataFrame(summary).to_string(index=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--phase", choices=["calibration", "forecast", "score"], required=True
    )
    args = parser.parse_args()
    spec = read_spec(args.config)
    guard(spec)
    torch.set_num_threads(1)
    root = ROOT / spec["output_root"]
    out = root / args.phase
    out.mkdir(parents=True, exist_ok=False)
    began = time.monotonic()
    event(
        out,
        "started",
        git_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        config_sha256=sha(ROOT / args.config),
    )
    try:
        if args.phase == "calibration":
            calibration_phase(spec, out)
        elif args.phase == "forecast":
            forecast_phase(spec, root, out)
        else:
            score_phase(spec, root, out)
    except Exception as error:
        event(
            out,
            "failed",
            error_type=type(error).__name__,
            error=str(error),
            elapsed_seconds=time.monotonic() - began,
        )
        raise
    else:
        event(
            out,
            "completed",
            elapsed_seconds=time.monotonic() - began,
            calls=CALLS.copy(),
        )
    finally:
        lock(out)


if __name__ == "__main__":
    main()
