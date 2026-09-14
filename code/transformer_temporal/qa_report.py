"""Cross-check rendered tables, numeric claims and local delivery links."""

import re

import numpy as np
import pandas as pd

from .core import B, ROOT, read_json, sha, spec, utc, write_json


def main():
    cfg = spec()
    root = ROOT / cfg["out"]
    report = ROOT / "docs/ootang_transformer_temporal_results.v1.0.md"
    text = report.read_text()
    record = read_json(root / "analysis/report_tables.json")
    assert sha(report) == record["report_sha256"]
    numeric = 0
    rows = 0
    for key, table in record["tables"].items():
        body = text.split("<!-- table:" + key + " -->")[1].split(
            "<!-- endtable:" + key + " -->"
        )[0]
        lines = [line.strip() for line in body.splitlines() if line.startswith("|")]
        cells = [[v.strip() for v in line.strip("|").split("|")] for line in lines]
        assert cells[0] == table["headers"]
        assert cells[2:] == [[str(v) for v in row] for row in table["rows"]]
        rows += len(table["rows"])
    s = pd.read_csv(root / "analysis/phase_summary.csv").set_index(["origin", "method"])
    p = pd.read_csv(root / "analysis/metrics_by_point.csv").set_index(
        ["origin", "method", "point"]
    )
    for r in record["numeric"]:
        value = (
            s.loc[(r["origin"], r["method"]), r["metric"]]
            if r["point"] is None
            else p.loc[(r["origin"], r["method"], r["point"]), r["metric"]]
        )
        assert float(value) == r["value"]
        numeric += 1
    for row in record["tables"]["probability"]["rows"]:
        names = {"B+": B, "历史选 α": "ALPHA_SELECTED", "历史选 λ": "LAMBDA_SELECTED"}
        assert (
            row[4] == f"{100 * s.loc[(int(row[0]), names[row[1]]), 'coverage90']:.2f}"
        )
    seeds = pd.read_csv(root / "analysis/seed_summary.csv").set_index(
        ["origin", "method", "seed"]
    )
    for row in record["tables"]["final_seeds"]["rows"]:
        i = int(row[0])
        expected = [
            f"{seeds.loc[(1168, k, i), metric]:.6f}"
            for k in ["ALPHA_SELECTED", "LAMBDA_SELECTED"]
            for metric in ["mae", "rmse"]
        ]
        assert row[1:] == expected
    for row in record["tables"]["choices"]["rows"]:
        n = int(row[0])
        a = read_json(root / f"origin_{n}/alpha_selection.json")
        reg = read_json(root / f"origin_{n}/lambda_selection.json")
        assert a["selection_indices"] == [int(x) for x in re.findall(r"\d+", row[1])]
        assert a["calibration_indices"] == [int(x) for x in re.findall(r"\d+", row[2])]
        assert float(row[4]) == cfg["lambdas"][reg["selected"]]
    expected = {
        "final_selected_rmse_change_percent": 100
        * (s.loc[(1168, "LAMBDA_SELECTED"), "rmse"] / s.loc[(1168, B), "rmse"] - 1),
        "final_half_rmse_change_percent": 100
        * (s.loc[(1168, "A05"), "rmse"] / s.loc[(1168, B), "rmse"] - 1),
        "origin972_lambda_rmse_change_percent": 100
        * (s.loc[(972, "LAMBDA_SELECTED"), "rmse"] / s.loc[(972, B), "rmse"] - 1),
    }
    assert expected == record["derived"]
    for n in cfg["origins"][1:]:
        a = s.loc[(n, "ALPHA_SELECTED")]
        reg = s.loc[(n, "LAMBDA_SELECTED")]
        if n < 1168:
            drift = s.loc[(n, "DRIFT1")]
            assert all(drift[k] < min(a[k], reg[k]) for k in ["mae", "rmse"])
        else:
            np.testing.assert_array_equal(
                a[["mae", "rmse", "crps"]].to_numpy(),
                reg[["mae", "rmse", "crps"]].to_numpy(),
            )
            assert reg["rmse"] > s.loc[(n, B), "rmse"]
    for k in ["L033", "L3"]:
        assert all(
            s.loc[(1168, k), m] > s.loc[(1168, "L1"), m] for m in ["mae", "rmse"]
        )
    links = []
    for target in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", text):
        if target.startswith("http"):
            continue
        path = (
            (report.parent / target).resolve()
            if not target.startswith("/")
            else ROOT / target
        )
        assert path.exists(), path
        links.append(str(path))
    qa = dict(
        status="passed",
        time_utc=utc(),
        tables=len(record["tables"]),
        rows=rows,
        traced_numeric_values=numeric,
        derived_ratios=3,
        local_links=len(links),
        source_numerical_audit="verification_v1/receipt.json",
        report_sha256=sha(report),
    )
    write_json(root / "analysis/report_qa.json", qa)
    print(qa)


if __name__ == "__main__":
    main()
