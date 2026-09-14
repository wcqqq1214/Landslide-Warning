"""Read-only cross-check of delivered Markdown/CSV against frozen score files."""

import re

import numpy as np
import pandas as pd

from .core import ROOT, guard_sources, read_json, sha, spec, utc, write_json
from .figures import NAMES


def main():
    cfg = spec()
    root = ROOT / cfg["out"]
    out = root / "analysis"
    report = ROOT / "docs/ootang_sequence_conditional_results.v1.0.md"
    text = report.read_text()
    counts = {"numeric_values": 0, "table_rows": 0, "csv_cells": 0}
    blocks = []
    current = []
    for line in text.splitlines() + [""]:
        if line.startswith("|"):
            current.append([s.strip() for s in line.strip("|").split("|")])
        elif current:
            blocks.append(current)
            current = []
    assert len(blocks) == 6

    def scalar(actual, expected, percent=False):
        value = float(actual.rstrip("%"))
        expected = float(expected) * (100 if percent else 1)
        assert (
            abs(value - expected) <= 5.00001e-5
            if not percent
            else abs(value - expected) <= 0.00500001
        )
        counts["numeric_values"] += 1

    def metrics_table(rows, phase):
        expected = pd.read_csv(root / phase / "summary.csv").set_index("model")
        assert len(rows) == len(cfg["methods"])
        keys = ["mae", "rmse", "crps", "interval_score90", "coverage90", "width90"]
        for row, m in zip(rows, cfg["methods"]):
            assert row[0] == NAMES[m]
            for cell, key in zip(row[1:], keys):
                scalar(cell, expected.loc[m, key], key.startswith("coverage"))
            counts["table_rows"] += 1

    metrics_table(blocks[1][2:], "final_exploratory")
    metrics_table(blocks[3][2:], "development")
    point = pd.read_csv(root / "final_exploratory/metrics_by_point.csv").set_index(
        ["model", "point"]
    )
    for row, p in zip(blocks[2][2:], cfg["points"]):
        assert row[0] == p
        for cell, m in zip(row[1:], cfg["methods"]):
            scalar(cell, point.loc[(m, p), "rmse"])
        counts["table_rows"] += 1
    fit = (
        pd.read_csv(root / "final_exploratory/fitting_by_point.csv")
        .groupby("model", sort=False)[["mae", "rmse"]]
        .mean()
    )
    assert len(blocks[5][2:]) == len(fit)
    for row, (m, values) in zip(blocks[5][2:], fit.iterrows()):
        assert row[0] == NAMES[m]
        scalar(row[1], values.mae)
        scalar(row[2], values.rmse)
        counts["table_rows"] += 1
    pairrows = iter(blocks[4][2:])
    for phase in ("development", "final_exploratory"):
        s = pd.read_csv(root / phase / "summary.csv").set_index("model")
        seeds = (
            pd.read_csv(root / phase / "seed_metrics.csv")
            .groupby(["model", "seed"])[["mae", "rmse", "crps", "interval_score90"]]
            .mean()
        )
        for family, pair in cfg["families"].items():
            row = next(pairrows)
            flags = []
            for keys in (("mae", "rmse"), ("crps", "interval_score90")):
                ensemble = all(s.loc[pair[1], k] < s.loc[pair[0], k] for k in keys)
                nseed = sum(
                    all(
                        seeds.loc[(pair[1], seed), k] < seeds.loc[(pair[0], seed), k]
                        for k in keys
                    )
                    for seed in (0, 1, 2)
                )
                flags.extend(["是" if ensemble else "否", str(nseed)])
            assert row[2:] == flags
            counts["table_rows"] += 1

    def same_frame(a, b):
        assert list(a.columns) == list(b.columns) and a.shape == b.shape
        for key in a:
            if pd.api.types.is_numeric_dtype(a[key]):
                np.testing.assert_allclose(a[key], b[key], atol=1e-10, rtol=0)
            else:
                np.testing.assert_array_equal(a[key], b[key])
            counts["csv_cells"] += len(a)

    for filename, source in (
        ("phase_summary", "summary"),
        ("metrics_by_point", "metrics_by_point"),
    ):
        expected = pd.concat(
            [
                pd.read_csv(root / p / (source + ".csv")).assign(phase=p)
                for p in ("development", "final_exploratory")
            ],
            ignore_index=True,
        )
        same_frame(pd.read_csv(out / (filename + ".csv")), expected)
    for filename, group in (
        ("fitting_summary", ["model"]),
        ("seed_summary", ["model", "seed"]),
    ):
        original = (
            "fitting_by_point" if filename == "fitting_summary" else "seed_metrics"
        )
        keys = (
            ["mae", "rmse"]
            if original == "fitting_by_point"
            else ["mae", "rmse", "crps", "interval_score90"]
        )
        expected = pd.concat(
            [
                pd.read_csv(root / p / (original + ".csv"))
                .groupby(group, sort=False)[keys]
                .mean()
                .reset_index()
                .assign(phase=p)
                for p in ("development", "final_exploratory")
            ],
            ignore_index=True,
        )
        same_frame(pd.read_csv(out / (filename + ".csv")), expected)
    same_frame(
        pd.read_csv(out / "comparisons.csv"),
        pd.read_csv(root / "verification_v1/paired_comparisons.csv"),
    )
    selection = read_json(root / "selection.json")
    frozen = read_json(root / "final_exploratory/frozen_selection_evaluation.json")
    assert selection == frozen["selection"]
    final = pd.read_csv(root / "final_exploratory/summary.csv").set_index("model")
    outcome = read_json(out / "outcome.json")
    assert final.mae.idxmin() == outcome["final_mean_rank_minimum"]
    assert final.rmse.idxmin() == outcome["final_rmse_minimum"]
    assert final.crps.idxmin() == outcome["final_probability_rank_minimum"]
    for phase in ("development", "final_exploratory"):
        gates = read_json(root / phase / "effect_gates.json")
        assert (
            sum(v["joint_pass"] for v in gates.values())
            == outcome["joint_pass_counts"][phase]
        )
    links = 0
    pending = []
    for target in re.findall(r"\]\(([^)]+)\)", text):
        if target.startswith("http"):
            continue
        path = (report.parent / target.split("#")[0]).resolve()
        if path.name == "final_receipt.json" and not path.exists():
            pending.append(str(path))
            continue
        assert path.is_file(), target
        links += 1
    assert guard_sources() == 73
    files = (
        [report]
        + list(out.glob("*.csv"))
        + [
            out / "outcome.json",
            ROOT / "code/sequence_conditional/report.py",
            ROOT / "code/sequence_conditional/qa_report.py",
        ]
    )
    write_json(
        out / "delivery_checks.json",
        dict(
            status="passed",
            time_utc=utc(),
            counts=counts,
            local_report_links_verified=links,
            pending_final_receipt_links=pending,
            source_files=73,
            no_new_fits=True,
            files={str(p.relative_to(ROOT)): sha(p) for p in files},
        ),
    )
    print(read_json(out / "delivery_checks.json"))


if __name__ == "__main__":
    main()
