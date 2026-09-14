"""Descriptive frozen comparisons and complete curve data, using saved forecasts."""

import argparse
import json

import numpy as np
import pandas as pd
from scipy.special import ndtri

from short_horizon.common import ROOT, load_spec, save_json, sha, now
from short_horizon.data import observations
from short_horizon.evaluation import gates
from short_horizon.run import lock_phase
from .models import ARMS
from .run import arrays


def write(frame, path):
    frame.to_csv(path, index=False, float_format="%.15g")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    spec = load_spec(args.config)
    root = ROOT / spec["output_root"]
    out = root / "analysis"
    out.mkdir(exist_ok=False)
    if not json.loads((root / "verification/receipt.json").read_text())["passed"]:
        raise ValueError("Saved-forecast verification must pass before analysis")
    y, _, dates = observations(spec)
    summary, metrics, curves, seed_rows = [], [], [], []
    source_hashes = {}
    for phase, (_, end) in spec["stages"].items():
        for filename, frames in (
            ("summary_by_horizon.csv", summary),
            ("metrics_by_point_horizon.csv", metrics),
        ):
            source = root / phase / filename
            frame = pd.read_csv(source)
            frame.insert(0, "phase", phase)
            frames.append(frame)
            source_hashes[str(source.relative_to(ROOT))] = sha(source)
        for name in spec["models"]:
            path = root / phase / (name + ".npz")
            pred = arrays(path)
            source_hashes[str(path.relative_to(ROOT))] = sha(path)
            for k in range(7):
                mask = pred["origins"] + k < end
                origins = pred["origins"][mask]
                for p, point in enumerate(spec["points"]):
                    mu, sigma = pred["mean"][mask, k, p], pred["sigma"][mask, k, p]
                    columns = dict(
                        phase=phase,
                        model=name,
                        horizon=k + 1,
                        point=point,
                        origin=origins,
                        last_observed_date=dates[origins - 1],
                        target_date=dates[origins + k],
                        observed_mm=y[origins + k, p],
                        mean_mm=mu,
                        sigma_mm=sigma,
                    )
                    for level in spec["calibration"]["levels"]:
                        q = ndtri((1 + level) / 2)
                        columns[f"lower{round(100 * level)}_mm"] = mu - q * sigma
                        columns[f"upper{round(100 * level)}_mm"] = mu + q * sigma
                    curves.append(pd.DataFrame(columns))
        for name in ARMS:
            path = root / phase / (name + "_seed_means.npz")
            seeds = arrays(path)["mean"]
            origins = arrays(root / phase / (name + ".npz"))["origins"]
            source_hashes[str(path.relative_to(ROOT))] = sha(path)
            for seed, mu in zip(spec["neural"]["seeds"], seeds):
                for k in range(7):
                    valid = origins + k < end
                    errors = mu[valid, k] - y[origins[valid] + k]
                    for p, point in enumerate(spec["points"]):
                        seed_rows.append(
                            dict(
                                phase=phase,
                                model=name,
                                seed=seed,
                                horizon=k + 1,
                                point=point,
                                n=int(valid.sum()),
                                mae=float(abs(errors[:, p]).mean()),
                                rmse=float(np.sqrt(np.mean(errors[:, p] ** 2))),
                            )
                        )
    summary = pd.concat(summary, ignore_index=True)
    metrics = pd.concat(metrics, ignore_index=True)
    curves = pd.concat(curves, ignore_index=True)
    seeds = pd.DataFrame(seed_rows)
    write(summary, out / "summary_by_horizon.csv")
    write(metrics, out / "metrics_by_point_horizon.csv")
    write(curves, out / "forecasts_long.csv")
    write(seeds, out / "seed_mean_metrics.csv")
    comparisons, point_comparisons, gate_rows = [], [], []
    key_metrics = ("mae", "rmse", "crps", "interval_score90", "coverage90", "width90")
    for phase in ("development", "later_exploratory"):
        s = summary[summary.phase == phase].set_index(["model", "horizon"])
        m = metrics[metrics.phase == phase].set_index(["model", "horizon", "point"])
        pairs = [
            (arm, base) for arm in ARMS for base in ("B_ANCHOR", "DRIFT1", "RR_DIRECT")
        ]
        pairs.append(("TCN_BRES", "TCN_DIRECT"))
        for h in range(1, 8):
            for candidate, reference in pairs:
                c, b = s.loc[(candidate, h)], s.loc[(reference, h)]
                row = dict(
                    phase=phase, horizon=h, candidate=candidate, reference=reference
                )
                for key in key_metrics:
                    row[key + "_difference"] = float(c[key] - b[key])
                    row[key + "_ratio"] = (
                        float(c[key] / b[key]) if b[key] != 0 else None
                    )
                row["mean_win"] = bool(c.mae < b.mae - 1e-8 and c.rmse < b.rmse - 1e-8)
                row["probability_score_win"] = bool(
                    c.crps < b.crps - 1e-8
                    and c.interval_score90 < b.interval_score90 - 1e-8
                )
                row["candidate_average_coverage_pass"] = bool(
                    0.85 <= c.coverage90 <= 0.95
                )
                comparisons.append(row)
                for point in spec["points"]:
                    c, b = m.loc[(candidate, h, point)], m.loc[(reference, h, point)]
                    point_comparisons.append(
                        dict(
                            phase=phase,
                            horizon=h,
                            point=point,
                            candidate=candidate,
                            reference=reference,
                            **{
                                key + "_difference": float(c[key] - b[key])
                                for key in key_metrics
                            },
                        )
                    )
            for name in spec["models"]:
                result = gates(
                    spec,
                    metrics[metrics.phase == phase],
                    summary[summary.phase == phase],
                    name,
                    h,
                )
                gate_rows.append(
                    dict(
                        phase=phase,
                        model=name,
                        horizon=h,
                        passed=result["passed"],
                        **result["checks"],
                    )
                )
    comparisons = pd.DataFrame(comparisons)
    point_comparisons = pd.DataFrame(point_comparisons)
    gate_frame = pd.DataFrame(gate_rows)
    write(comparisons, out / "comparisons.csv")
    write(point_comparisons, out / "comparisons_by_point.csv")
    write(gate_frame, out / "working_conditions.csv")
    paired_seed = []
    for phase in spec["stages"]:
        rows = seeds[seeds.phase == phase].set_index(
            ["model", "seed", "horizon", "point"]
        )
        for seed in spec["neural"]["seeds"]:
            for h in range(1, 8):
                for point in spec["points"]:
                    direct = rows.loc[("TCN_DIRECT", seed, h, point)]
                    bres = rows.loc[("TCN_BRES", seed, h, point)]
                    paired_seed.append(
                        dict(
                            phase=phase,
                            seed=seed,
                            horizon=h,
                            point=point,
                            mae_difference=float(bres.mae - direct.mae),
                            rmse_difference=float(bres.rmse - direct.rmse),
                        )
                    )
    write(pd.DataFrame(paired_seed), out / "residual_seed_differences.csv")
    locked = json.loads((root / "selection.json").read_text())["by_horizon"]
    write(
        pd.DataFrame(
            [
                {
                    k: r[k]
                    for k in ("horizon", "mean_best", "probability_best", "recommended")
                }
                for r in locked
            ]
        ),
        out / "development_selection.csv",
    )
    history = []
    for phase in ("development", "later_exploratory"):
        path = ROOT / spec["legacy_root"] / phase / "summary_by_horizon.csv"
        old = pd.read_csv(path)
        old = old[old.model.isin(spec["historical_only"])].copy()
        old.insert(0, "phase", phase)
        old["role"] = "historical_only_excluded_from_selection_and_gates"
        old["limitation"] = old.model.map(
            {
                "PINN_EQ": "v4_physical_acceptance_failed",
                "C16_CORE_RULES": "frozen_online_feedback_historical_exposure",
                "CL_DIRECT": "v4_inputs_and_separately_selected_checkpoints",
                "CL_BRES": "v4_inputs_and_separately_selected_checkpoints",
            }
        )
        history.append(old)
        source_hashes[str(path.relative_to(ROOT))] = sha(path)
    write(pd.concat(history, ignore_index=True), out / "historical_supplement.csv")
    outcome = {}
    for phase in ("development", "later_exploratory"):
        outcome[phase] = {}
        for name in ARMS:
            outcome[phase][name] = {
                "joint_conditions_pass_horizons": gate_frame[
                    (gate_frame.phase == phase)
                    & (gate_frame.model == name)
                    & gate_frame.passed
                ].horizon.tolist()
            }
            for reference in ("B_ANCHOR", "DRIFT1", "RR_DIRECT"):
                rows = comparisons[
                    (comparisons.phase == phase)
                    & (comparisons.candidate == name)
                    & (comparisons.reference == reference)
                ]
                outcome[phase][name][reference] = {
                    "mean_win_horizons": rows[rows.mean_win].horizon.tolist(),
                    "probability_score_win_horizons": rows[
                        rows.probability_score_win
                    ].horizon.tolist(),
                }
        pair = comparisons[
            (comparisons.phase == phase)
            & (comparisons.candidate == "TCN_BRES")
            & (comparisons.reference == "TCN_DIRECT")
        ]
        outcome[phase]["residual_vs_direct"] = {
            "mean_win_horizons": pair[pair.mean_win].horizon.tolist(),
            "probability_score_win_horizons": pair[
                pair.probability_score_win
            ].horizon.tolist(),
            "interval_score_regression_horizons": pair[
                pair.interval_score90_difference > 1e-8
            ].horizon.tolist(),
        }
    save_json(
        out / "conclusions.json",
        dict(
            time_utc=now(),
            outcome=outcome,
            curve_rows=len(curves),
            summary_rows=len(summary),
            metric_rows=len(metrics),
            seed_rows=len(seeds),
            source_sha256=source_hashes,
            descriptive_only=True,
            no_new_training=True,
            no_later_reselection=True,
            uncertainty="marginal empirical Gaussian prediction intervals; not confidence intervals on skill",
        ),
    )
    lock_phase(out)
    print(
        json.dumps(
            dict(curve_rows=len(curves), summary_rows=len(summary), outcome=outcome),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
