"""Read-back verification of report tables, cross scores and local links."""

import json
import re

import numpy as np
import pandas as pd

from .core import (
    NEW,
    OLD,
    ROOT,
    load_npz,
    read_json,
    read_labels,
    scores,
    sha,
    spec,
    utc,
    write_json,
)


def main():
    cfg = spec()
    root = ROOT / cfg["out"]
    analysis = root / "analysis"
    report = ROOT / "docs/ootang_transformer_regularization_results.v1.0.md"
    text = report.read_text()
    source = read_json(analysis / "report_sources.json")
    assert sha(report) == source["report_sha256"]
    for p, h in source["files"].items():
        assert sha(ROOT / p) == h, p
    arrays = load_npz(ROOT / cfg["figures"] / "v1/source_arrays.npz")
    truth = read_labels(ROOT / cfg["data"], 1461)
    np.testing.assert_array_equal(arrays["observed"], truth)
    # Cross terms are recomputed using the original scoring implementation,
    # separate from the explicit formulas used to create the audit tables.
    cross = pd.read_csv(analysis / "cross_scoring.csv")
    cells = 0
    for phase, (n, end) in cfg["stages"].items():
        if phase == "internal":
            continue
        issued = load_npz(root / phase / "issued_distribution.npz")
        for mean in cfg["cross_diagnostics"]:
            for sigma in cfg["cross_diagnostics"]:
                rows = scores(
                    truth[n:end], issued[mean + "__mean"], issued[sigma + "__sigma"]
                )
                actual = cross[
                    (cross.phase == phase)
                    & (cross.mean_source == mean)
                    & (cross.sigma_source == sigma)
                ].set_index("point")
                for row in rows:
                    for key, value in row.items():
                        if key in ("point", "n"):
                            continue
                        np.testing.assert_allclose(
                            actual.loc[row["point"], key], value, atol=1e-8, rtol=0
                        )
                        cells += 1
    groups = []
    current = []
    for line in text.splitlines() + [""]:
        if line.startswith("|"):
            current.append([s.strip() for s in line.strip("|").split("|")])
        elif current:
            groups.append(current[2:])
            current = []
    assert len(groups) == 7
    summary = pd.read_csv(analysis / "phase_summary.csv").set_index(["phase", "model"])
    points = pd.read_csv(analysis / "metrics_by_point.csv").set_index(
        ["phase", "model", "point"]
    )
    pairing = pd.read_csv(analysis / "regularization_pairing.csv")
    seed = pd.read_csv(analysis / "seed_summary.csv").set_index(
        ["phase", "model", "seed"]
    )
    fit = pd.read_csv(analysis / "fitting_summary.csv").set_index(["phase", "model"])
    cross_avg = cross.groupby(["phase", "mean_source", "sigma_source"]).mean(
        numeric_only=True
    )
    checks = 0

    def number(cell, value, percent=False, decimals=4):
        nonlocal checks
        expected = f"{value * (100 if percent else 1):.{decimals}f}" + (
            "%" if percent else ""
        )
        assert cell == expected, (cell, expected)
        checks += 1

    for g, phase in zip(groups[:2], ("development", "final_exploratory")):
        assert len(g) == 5
        for row, m in zip(g, cfg["methods"]):
            for i, key in enumerate(
                ("mae", "rmse", "crps", "interval_score90", "coverage90", "width90"), 1
            ):
                number(
                    row[i],
                    summary.loc[(phase, m), key],
                    key == "coverage90",
                    2 if key == "coverage90" else 4,
                )
    for row, point in zip(groups[2], cfg["points"]):
        assert row[0] == point
        for col, m in enumerate(("BPLUS_CONTINUOUS", OLD, NEW), 1):
            number(row[col], points.loc[("final_exploratory", m, point), "rmse"])
    for row, r in zip(groups[3], pairing.itertuples()):
        assert row[2] == ("是" if r.ensemble_improved else "否")
        assert row[3] == str(r.seed_agreement)
        assert row[4] == ("通过" if r.paired_pass else "未通过")
    for row, s in zip(groups[4], (0, 1, 2)):
        assert row[0] == str(s)
        values = [
            seed.loc[("final_exploratory", m, s), k]
            for k in ("mae", "rmse")
            for m in (OLD, NEW)
        ]
        for cell, value in zip(row[1:], values):
            number(cell, value)
    for row, (phase, m) in zip(
        groups[5],
        [
            (phase, m)
            for phase in ("development", "final_exploratory")
            for m in ("BPLUS_CONTINUOUS", OLD, NEW)
        ],
    ):
        for cell, key in zip(row[2:], ("mae", "rmse")):
            number(cell, fit.loc[(phase, m), key])
    for row, m in zip(groups[6], cfg["cross_diagnostics"]):
        for cell, s in zip(row[1:], cfg["cross_diagnostics"]):
            number(cell, cross_avg.loc[("final_exploratory", m, s), "crps"])
    outcome = read_json(analysis / "outcome.json")
    assert outcome["phases"][1]["RR_COND_probability_improved"]
    assert "最终两项概率评分均优于岭回归" in text
    for phase in ("development", "final_exploratory"):
        for p in cfg["points"]:
            v = points.loc[(phase, NEW, p)]
            b = points.loc[(phase, "BPLUS_CONTINUOUS", p)]
            if phase == "development" or p == "MJ3":
                assert v.mae > b.mae and v.rmse > b.rmse
            else:
                assert v.mae < b.mae and v.rmse < b.rmse
    links = []
    for link in re.findall(r"\]\(([^)]+)\)", text):
        if link.startswith(("https:", "http:", "#")):
            continue
        path = (report.parent / link.split("#")[0]).resolve()
        assert path.exists(), link
        links.append(link)
    # Read-back copied CSV values and ordering, not only existence.
    csv_cells = 0
    for p in analysis.glob("*.csv"):
        original = root / "verification_v1" / p.name
        assert p.read_bytes() == original.read_bytes()
        data = pd.read_csv(p)
        csv_cells += data.size
    result = dict(
        status="passed",
        time_utc=utc(),
        report_sha256=sha(report),
        tables=len(groups),
        table_rows=sum(map(len, groups)),
        numeric_metric_cells=checks,
        independent_cross_score_cells=cells,
        readback_csv_cells=csv_cells,
        verified_local_links=len(links),
        scope="tables, point/seed caveats, baseline directions, saved diagnostics; no new fitting",
    )
    write_json(analysis / "delivery_checks.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
