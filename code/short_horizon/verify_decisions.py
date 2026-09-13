"""Independent reconstruction of frozen selection, fitting tables and plot data."""

import json
import argparse
import numpy as np
import pandas as pd

from .common import ROOT, load_spec, save_json, sha, now
from .data import observations
from .verify import arrays, close


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    spec = load_spec(args.config)
    root = ROOT / spec["output_root"]
    y, _, dates = observations(spec)
    internal = json.loads((root / "internal_selection.json").read_text())
    selection = json.loads((root / "selection.json").read_text())
    tol = 1e-8
    choices = []

    def q_score(path, name):
        s = pd.read_csv(path).set_index(["model", "horizon"])
        return (
            sum(
                0.5
                * (
                    s.loc[(name, h), "rmse"] / max(s.loc[("DRIFT1", h), "rmse"], tol)
                    + s.loc[(name, h), "crps"] / max(s.loc[("DRIFT1", h), "crps"], tol)
                )
                for h in range(1, 8)
            )
            / 7
        )

    for name, step in internal["checkpoints"].items():
        scores = {
            n: q_score(
                root / "inner" / f"{name}_step_{n}" / "summary_by_horizon_common.csv",
                name,
            )
            for n in [50, 100, 200, 400]
        }
        best = min(n for n, v in scores.items() if v <= min(scores.values()) + tol)
        assert best == step
        choices.append(dict(model=name, checkpoint=best))
    ridge = {
        a: sum(
            q_score(
                root / "inner" / f"ridge_a{a:g}" / "summary_by_horizon_common.csv", n
            )
            for n in ["RR_DIRECT", "RR_BRES"]
        )
        / 2
        for a in [0.001, 0.01, 0.1, 1, 10]
    }
    assert internal["ridge_alpha"] == min(
        a for a, v in ridge.items() if v <= min(ridge.values()) + tol
    )

    def choose(frame, columns):
        remaining = sorted(frame.index)
        for col in columns:
            value = min(frame.loc[n, col] for n in remaining)
            remaining = [n for n in remaining if frame.loc[n, col] <= value + tol]
        return sorted(remaining)[0]

    checks = 0
    later = []
    for phase in ["development", "later_exploratory"]:
        S = pd.read_csv(root / phase / "summary_by_horizon.csv")
        M = pd.read_csv(root / phase / "metrics_by_point_horizon.csv")
        physics = json.loads(
            (root / phase / "PINN_EQ_physical_audit.json").read_text()
        )["passed"]
        for h in range(1, 8):
            s = S[S.horizon == h].set_index("model")
            m = M[M.horizon == h].set_index(["model", "point"])
            names = sorted(set(s.index) - set(spec["selection_excluded"]))
            passed = {}
            q = {}
            for name in names:
                c, b, d = s.loc[name], s.loc["B_ANCHOR"], s.loc["DRIFT1"]
                rules = [
                    c[k] < b[k] - tol
                    for k in ["mae", "rmse", "crps", "interval_score90"]
                ]
                rules += [c[k] <= d[k] + tol for k in ["rmse", "crps"]] + [
                    0.85 <= c.coverage90 <= 0.95
                ]
                for point in spec["points"]:
                    a, base = m.loc[(name, point)], m.loc[("B_ANCHOR", point)]
                    rules += [a[k] <= base[k] + tol for k in ["mae", "rmse"]]
                    rules += [
                        a[k] <= 1.05 * base[k] + tol
                        for k in ["crps", "interval_score90"]
                    ]
                    rules += [0.8 <= a.coverage90 <= 0.98]
                rules += [physics if name == "PINN_EQ" else True]
                checks += len(rules)
                passed[name] = all(rules)
                q[name] = 0.5 * (c.rmse / max(d.rmse, tol) + c.crps / max(d.crps, tol))
            saved = selection["by_horizon"][h - 1]
            if phase == "development":
                assert choose(s.loc[names], ["rmse", "mae"]) == saved["mean_best"]
                assert (
                    choose(s.loc[names], ["crps", "interval_score90"])
                    == saved["probability_best"]
                )
                eligible = [n for n in names if passed[n]]
                best = (
                    sorted(
                        n for n in eligible if q[n] <= min(q[j] for j in eligible) + tol
                    )[0]
                    if eligible
                    else None
                )
                assert best == saved["recommended"]
                assert all(passed[n] == saved["gates"][n]["passed"] for n in names)
                for name in names:
                    close(np.array(q[name]), np.array(saved["q"][name]), 1e-10)
            else:
                name = saved["recommended"]
                later.append(
                    dict(
                        horizon=h,
                        model=name,
                        passed=False if name is None else passed[name],
                    )
                )
    fitting_cells = 0
    for phase, (start, end) in spec["stages"].items():
        for file in (root / phase / "fitting").glob("*_metrics.csv"):
            a = arrays(file.with_name(file.name.replace("_metrics.csv", ".npz")))
            frame = pd.read_csv(file)
            for _, r in frame.iterrows():
                j = spec["points"].index(r.point)
                err = (
                    a["mean"][:, r.horizon - 1, j] - y[a["origins"] + r.horizon - 1, j]
                )
                close(np.array(r.mae), np.array(np.mean(abs(err))), 1e-9)
                close(np.array(r.rmse), np.array(np.sqrt(np.mean(err * err))), 1e-9)
                fitting_cells += 2
    a = root / "analysis"
    chosen = pd.read_csv(a / "selection_by_horizon.csv")
    assert chosen.later_pass.tolist() == [r["passed"] for r in later]
    paired = pd.read_csv(a / "paired_differences.csv")
    for _, r in paired.iterrows():
        s = pd.read_csv(root / r.phase / "summary_by_horizon.csv").set_index(
            ["model", "horizon"]
        )
        close(
            np.array(r.difference),
            np.array(
                s.loc[(r.candidate, r.horizon), r.metric]
                - s.loc[(r.reference, r.horizon), r.metric]
            ),
            1e-10,
        )
    fig = ROOT / spec["figures_root"]
    curve = pd.read_csv(fig / "curve_source_data.csv")
    count = 0
    for (model, h, point), g in curve.groupby(["model", "horizon", "point"]):
        a = arrays(root / "later_exploratory" / (model + ".npz"))
        p = spec["points"].index(point)
        mask = a["origins"] + h <= 1461
        assert np.array_equal(g.origin, a["origins"][mask])
        ids = a["origins"][mask] + h - 1
        close(g.observed.to_numpy(), y[ids, p], 1e-9)
        close(g["mean"].to_numpy(), a["mean"][mask, h - 1, p], 1e-9)
        close(g.sigma.to_numpy(), a["sigma"][mask, h - 1, p], 1e-9)
        close(
            g.increment_observed.to_numpy(),
            y[ids, p] - y[a["origins"][mask] - 1, p],
            1e-9,
        )
        assert np.array_equal(
            pd.to_datetime(g.target_date).to_numpy(),
            pd.to_datetime(dates[ids]).to_numpy(),
        )
        count += len(g)
    save_json(
        root / "verification/decision_and_figure_receipt.json",
        dict(
            passed=True,
            time_utc=now(),
            internal_choices=choices,
            ridge_alpha=internal["ridge_alpha"],
            independent_gate_checks=checks,
            later_locked_choices=later,
            fitting_score_cells=fitting_cells,
            paired_table_rows=len(paired),
            complete_plot_rows=count,
            selection_sha256=sha(root / "selection.json"),
            plot_data_sha256=sha(fig / "curve_source_data.csv"),
            new_training=0,
            physical_calls=0,
        ),
    )
    print(
        json.dumps(
            {
                "passed": True,
                "gate_checks": checks,
                "fitting_score_cells": fitting_cells,
                "plot_rows": count,
            }
        )
    )


if __name__ == "__main__":
    main()
