"""Fixed contrasts and descriptive paired statistics from frozen predictions."""

import argparse
import json
import math
import numpy as np
import pandas as pd
from scipy.special import ndtr

from .common import ROOT, load_spec, save_json, sha, now, check_deadline
from .data import observations
from .verify import arrays

PAIRS = [
    ("CL_BRES", "CL_DIRECT", "convlstm_residual"),
    ("RR_BRES", "RR_DIRECT", "ridge_residual"),
    ("PINN_EQ", "PINN_NOEQ", "equation_constraint"),
    ("C16_PHYS_RULES", "C16_CORE_RULES", "physical_error"),
]


def point_crps(y, mu, sd):
    z = (y - mu) / sd
    return sd * (
        z * (2 * ndtr(z) - 1)
        + 2 * np.exp(-z * z / 2) / math.sqrt(2 * math.pi)
        - 1 / math.sqrt(math.pi)
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    spec = load_spec(args.config)
    check_deadline(spec)
    root = ROOT / spec["output_root"]
    out = root / "analysis"
    out.mkdir(exist_ok=False)
    verification = json.loads((root / "verification/receipt.json").read_text())
    if not verification["passed"]:
        raise ValueError("Independent verification is not complete")
    y, _, dates = observations(spec)
    lock = json.loads((root / "selection.json").read_text())
    tables = []
    points = []
    fits = []
    byphase = {}
    raw = {}
    seeds = []
    for phase in spec["stages"]:
        byphase[phase] = pd.read_csv(root / phase / "summary_by_horizon.csv").set_index(
            ["model", "horizon"]
        )
        for source, dest in [
            ("summary_by_horizon.csv", tables),
            ("metrics_by_point_horizon.csv", points),
        ]:
            df = pd.read_csv(root / phase / source)
            df.insert(0, "phase", phase)
            df.insert(
                1,
                "scenario",
                np.where(
                    df.model == "B_ORACLE",
                    "known_future_drivers_secondary",
                    "past_only_main",
                ),
            )
            df.insert(3, "probability_model", df.model + "_G")
            dest.append(df)
        for p in (root / phase / "fitting").glob("*_metrics.csv"):
            df = pd.read_csv(p)
            df.insert(0, "phase", phase)
            fits.append(df)
        raw[phase] = {
            p.stem: arrays(p)
            for p in (root / phase).glob("*.npz")
            if p.with_name(p.stem + "_calibration.json").exists()
        }
        if phase != "inner":
            end = spec["stages"][phase][1]
            for name in ("CL_DIRECT", "CL_BRES", "PINN_EQ", "PINN_NOEQ"):
                a = arrays(root / phase / (name + "_seed_means.npz"))["mean"]
                origins = raw[phase][name]["origins"]
                for h in range(1, 8):
                    mask = origins + h <= end
                    for seed in range(3):
                        e = a[seed, mask, h - 1] - y[origins[mask] + h - 1]
                        seeds.append(
                            dict(
                                phase=phase,
                                model=name,
                                horizon=h,
                                seed=seed,
                                mae=abs(e).mean(),
                                rmse=np.sqrt(np.mean(e * e, axis=0)).mean(),
                                pooled_rmse=np.sqrt(np.mean(e * e)),
                            )
                        )
    pd.concat(tables).to_csv(
        out / "summary_by_horizon.csv", index=False, float_format="%.15g"
    )
    pd.concat(points).to_csv(
        out / "metrics_by_point_horizon.csv", index=False, float_format="%.15g"
    )
    pd.concat(fits).to_csv(
        out / "fitting_metrics.csv", index=False, float_format="%.15g"
    )
    pd.DataFrame(seeds).to_csv(
        out / "seed_mean_metrics.csv", index=False, float_format="%.15g"
    )
    later_checks = {
        r["horizon"]: r
        for r in json.loads(
            (root / "later_exploratory/frozen_selection_evaluation.json").read_text()
        )
    }
    rows = []
    contrasts = []
    families = []
    for r in lock["by_horizon"]:
        h = r["horizon"]
        chosen = r["recommended"]
        row = dict(
            horizon=h,
            mean_best=r["mean_best"],
            probability_best=r["probability_best"],
            recommended=chosen,
            later_pass=False
            if chosen is None
            else later_checks[h]["later_check"]["passed"],
        )
        for phase in ("development", "later_exploratory"):
            s = byphase[phase]
            for role in ("mean_best", "probability_best", "recommended"):
                n = r[role]
                if n:
                    for metric in (
                        "mae",
                        "rmse",
                        "pooled_rmse",
                        "crps",
                        "coverage90",
                        "width90",
                        "interval_score90",
                    ):
                        row[f"{phase}_{role}_{metric}"] = float(s.loc[(n, h), metric])
        rows.append(row)
        for family, names in [
            ("B+", ["B_ANCHOR"]),
            ("ConvLSTM", ["CL_DIRECT", "CL_BRES"]),
            ("PINN", ["PINN_EQ"]),
            ("Ridge", ["RR_DIRECT", "RR_BRES", "C16_CORE_RULES", "C16_PHYS_RULES"]),
        ]:
            dev = byphase["development"]
            best = min(
                names,
                key=lambda n: (dev.loc[(n, h), "rmse"], dev.loc[(n, h), "mae"], n),
            )
            for phase in ("development", "later_exploratory"):
                value = byphase[phase].loc[(best, h)].to_dict()
                families.append(
                    dict(
                        phase=phase,
                        family=family,
                        model=best,
                        horizon=h,
                        selection="development_minimum_mean_rmse",
                        physical_contract_pass=(False if family == "PINN" else True),
                        **value,
                    )
                )
        for phase in ("development", "later_exploratory"):
            s = byphase[phase]
            for a, b, contrast in PAIRS:
                for metric in (
                    "mae",
                    "rmse",
                    "crps",
                    "coverage90",
                    "width90",
                    "interval_score90",
                ):
                    contrasts.append(
                        dict(
                            phase=phase,
                            horizon=h,
                            contrast=contrast,
                            candidate=a,
                            reference=b,
                            metric=metric,
                            candidate_value=s.loc[(a, h), metric],
                            reference_value=s.loc[(b, h), metric],
                            difference=s.loc[(a, h), metric] - s.loc[(b, h), metric],
                        )
                    )
    pd.DataFrame(rows).to_csv(
        out / "selection_by_horizon.csv", index=False, float_format="%.15g"
    )
    pd.DataFrame(contrasts).to_csv(
        out / "paired_differences.csv", index=False, float_format="%.15g"
    )
    pd.DataFrame(families).to_csv(
        out / "family_representatives.csv", index=False, float_format="%.15g"
    )
    # Moving blocks sample issued origins, carrying all four points together.
    rng = np.random.default_rng(spec["bootstrap"]["seed"])
    stats = []
    sensitivity = []
    for r in lock["by_horizon"]:
        check_deadline(spec)
        h = r["horizon"]
        names = sorted(
            set(r[k] for k in ("mean_best", "probability_best", "recommended") if r[k])
        )
        end = spec["stages"]["later_exploratory"][1]
        origins = raw["later_exploratory"]["B_ANCHOR"]["origins"]
        mask = origins + h <= end
        N = int(mask.sum())
        length = spec["bootstrap"]["block_length"]
        B = spec["bootstrap"]["replicates"]
        starts = rng.integers(0, N - length + 1, size=(B, math.ceil(N / length)))
        ids = (starts[:, :, None] + np.arange(length)).reshape(B, -1)[:, :N]
        for name in names:
            a = raw["later_exploratory"][name]
            truth = y[origins[mask] + h - 1]
            ae = (a["mean"][mask, h - 1] - truth) ** 2
            ac = point_crps(truth, a["mean"][mask, h - 1], a["sigma"][mask, h - 1])
            for base in ("B_ANCHOR", "DRIFT1"):
                b = raw["later_exploratory"][base]
                be = (b["mean"][mask, h - 1] - truth) ** 2
                bc = point_crps(truth, b["mean"][mask, h - 1], b["sigma"][mask, h - 1])
                values = {
                    "rmse": np.sqrt(ae[ids].mean(1)).mean(1)
                    - np.sqrt(be[ids].mean(1)).mean(1),
                    "crps": (ac[ids] - bc[ids]).mean(axis=(1, 2)),
                }
                actual = {
                    "rmse": np.sqrt(ae.mean(0)).mean() - np.sqrt(be.mean(0)).mean(),
                    "crps": np.mean(ac - bc),
                }
                for metric, bootstrap in values.items():
                    lo, hi = np.quantile(bootstrap, [0.025, 0.975])
                    stats.append(
                        dict(
                            horizon=h,
                            model=name,
                            reference=base,
                            metric=metric,
                            n_origins=N,
                            block_days=length,
                            replicates=B,
                            seed=spec["bootstrap"]["seed"],
                            difference=actual[metric],
                            low95=lo,
                            high95=hi,
                            interpretation="descriptive_exposed_history_not_independent_validation",
                        )
                    )
                index = np.arange(0, N, 7)
                sensitivity.append(
                    dict(
                        horizon=h,
                        model=name,
                        reference=base,
                        n_origins=len(index),
                        first_origin=int(origins[mask][0]),
                        rmse_difference=np.sqrt(ae[index].mean(0)).mean()
                        - np.sqrt(be[index].mean(0)).mean(),
                        crps_difference=np.mean(ac[index] - bc[index]),
                        origin_stride_days=7,
                    )
                )
    pd.DataFrame(stats).to_csv(
        out / "paired_block_bootstrap.csv", index=False, float_format="%.15g"
    )
    pd.DataFrame(sensitivity).to_csv(
        out / "every_seventh_origin.csv", index=False, float_format="%.15g"
    )
    best = min(
        rows,
        key=lambda r: (r["development_mean_best_rmse"], r["horizon"], r["mean_best"]),
    )
    passed = [r["horizon"] for r in rows if r["later_pass"]]
    # Native legacy intervals are a clearly separate descriptive appendix.
    legacy = []
    for phase in ("development", "later_exploratory"):
        for directory in ("c16_fast_feedback_run3", "c18_information"):
            path = (
                ROOT
                / "results/ootang_rolling_v3/20260913"
                / directory
                / phase
                / "summary.csv"
            )
            if not path.exists():
                continue
            old = pd.read_csv(path)
            old = old[old.horizon <= 7].copy()
            old.insert(0, "phase", phase)
            old.insert(1, "source_family", directory)
            old["comparison_status"] = "historical_native_scale_not_main_ranking"
            legacy.append(old)
    if legacy:
        pd.concat(legacy).to_csv(
            out / "legacy_native_probability.csv", index=False, float_format="%.15g"
        )
    save_json(
        out / "conclusions.json",
        dict(
            created_utc=now(),
            selection_sha256=sha(root / "selection.json"),
            verification_sha256=sha(root / "verification/receipt.json"),
            best_development_mean_combination=dict(
                model=best["mean_best"],
                horizon=best["horizon"],
                development_rmse=best["development_mean_best_rmse"],
                later_rmse=best["later_exploratory_mean_best_rmse"],
            ),
            longest_jointly_qualified_horizon=max(passed) if passed else None,
            qualified_horizons=passed,
            physical_increment_established=False,
            real_warning_validated=False,
            main_claim="Prespecified rolling endpoint comparisons on the published daily sequence; no new independent data",
        ),
    )
    save_json(
        out / "artifact_manifest.json",
        {
            p.name: sha(p)
            for p in sorted(out.iterdir())
            if p.is_file() and p.name != "artifact_manifest.json"
        },
    )
    print(
        json.dumps(
            {
                "qualified_horizons": passed,
                "best_model": best["mean_best"],
                "best_horizon": best["horizon"],
                "bootstrap_rows": len(stats),
            }
        )
    )


if __name__ == "__main__":
    main()
