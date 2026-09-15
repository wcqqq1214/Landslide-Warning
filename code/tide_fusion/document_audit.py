"""Verify displayed fusion tables, stated effects, and delivered local links."""

import json
from pathlib import Path
import re
import subprocess

import pandas as pd

from tide_features.document_audit import tables
from . import core as c


def main():
    cfg, o = c.spec(), c.o
    root = c.ROOT / cfg["out"]
    assert c.guard() == 1272
    o.verify_lock(root / "audit_lock.json")
    o.verify_lock(c.ROOT / cfg["figures"] / "delivery_lock.json")

    def read(name):
        return pd.read_csv(root / "analysis" / name, float_precision="round_trip")

    summary = read("phase_summary.csv").set_index(["origin", "method"])
    points = read("metrics_by_point.csv").set_index(["origin", "method", "point"])
    comps = read("component_summary.csv").set_index(
        ["origin", "method", "seed", "point"]
    )
    pairs = o.read_json(root / "analysis/pairing.json")
    result = c.ROOT / "docs/ootang_tide_fusion_results.v1.0.md"
    tabs = tables(result)
    assert len(tabs) == 4 and "@@" not in result.read_text()
    cells, rows = 0, 0
    assert [v[0] for v in tabs[0][1:]] == cfg["controls"] + cfg["arms"]
    for row in tabs[0][1:]:
        assert row[1:] == [
            f"{summary.loc[(n, row[0]), 'rmse']:.6f}" for n in cfg["origins"][1:]
        ]
        cells += 3
        rows += 1
    assert [v[0] for v in tabs[1][1:]] == cfg["points"]
    for row in tabs[1][1:]:
        point = row[0]
        expected = [
            f"{points.loc[(1168, m, point), 'rmse']:.6f}"
            for m in [c.B, *cfg["new_arms"]]
        ]
        expected += [
            f"{100 * points.loc[(1168, m, point), 'coverage90']:.2f}%"
            for m in cfg["new_arms"]
        ]
        assert row[1:] == expected
        cells += 5
        rows += 1
    edges = [(m, c.B) for m in cfg["arms"]] + [
        tuple(v) for v in cfg["reporting"]["paired_edges"]
    ]
    assert [tuple(v[:2]) for v in tabs[2][1:]] == edges
    for row in tabs[2][1:]:
        a, b = row[:2]
        ps = [v for v in pairs if v["candidate"] == a and v["reference"] == b]
        assert len(ps) == 3
        assert row[2:] == [
            f"{sum(v[k] for v in ps)}/3"
            for k in ["mean_pass", "probability_pass", "joint_pass"]
        ]
        cells += 3
        rows += 1
    assert [v[0] for v in tabs[3][1:]] == cfg["points"]
    for row in tabs[3][1:]:
        point = row[0]
        expected = []
        for method, field in [
            ("TiDE_BOUND", "cap_mm"),
            ("TiDE_SPLIT", "hydro_abs_max"),
            ("TiDE_BOUND", "raw_abs_max"),
            ("TiDE_BOUND", "hydro_abs_max"),
        ]:
            expected.append(
                f"{comps.loc[(1168, method, 'ensemble', point), field]:.6f}"
            )
        assert row[1:] == expected
        cells += 4
        rows += 1
    tab = tables(c.ROOT / "README.md")[0]
    assert [int(v[0]) for v in tab[1:]] == cfg["origins"][1:]
    for row in tab[1:]:
        assert row[1:] == [
            f"{summary.loc[(int(row[0]), m), 'rmse']:.6f}" for m in [c.B, *cfg["arms"]]
        ]
        cells += 5
        rows += 1

    narrative_checks = []

    def claim(name, passed):
        assert passed, name
        narrative_checks.append(dict(claim=name, passed=True))

    for n in cfg["origins"][1:]:
        for metric in ["mae", "rmse", "crps", "interval_score90"]:
            claim(
                f"BOUND vs SPLIT ensemble improves {n}/{metric}",
                summary.loc[(n, "TiDE_BOUND"), metric]
                < summary.loc[(n, "TiDE_SPLIT"), metric],
            )
        for m in cfg["new_arms"]:
            for metric in ["mae", "rmse"]:
                claim(
                    f"{m} ensemble worse than KIN {n}/{metric}",
                    summary.loc[(n, m), metric] > summary.loc[(n, "TiDE_KIN"), metric],
                )
        for metric in ["mae", "rmse"]:
            claim(
                f"SPLIT ensemble worse than PHYS {n}/{metric}",
                summary.loc[(n, "TiDE_SPLIT"), metric]
                > summary.loc[(n, "TiDE_PHYS"), metric],
            )
    for reference, counts in [("TiDE_SPLIT", [3, 2, 1]), ("TiDE_KIN", [3, 1, 0])]:
        actual = [
            next(
                p["seed_both_improve"]
                for p in pairs
                if p["origin"] == n
                and p["candidate"] == "TiDE_BOUND"
                and p["reference"] == reference
            )
            for n in cfg["origins"][1:]
        ]
        claim("BOUND same-seed counts vs " + reference, actual == counts)
    for n, rounded in zip(cfg["origins"][1:], [-27.290514, -7.896767, -0.192368]):
        delta = (
            summary.loc[(n, "TiDE_BOUND"), "rmse"]
            - summary.loc[(n, "TiDE_SPLIT"), "rmse"]
        )
        claim(f"RMSE difference narrative {n}", round(delta, 6) == rounded)
    gain = 100 * (
        1 - summary.loc[(1168, "TiDE_BOUND"), "rmse"] / summary.loc[(1168, c.B), "rmse"]
    )
    claim("Final B+ relative RMSE improvement", round(gain, 6) == 0.943667)
    claim("Final B+ gain below frozen1% gate", gain < 1)
    for point, metric in [("MJ3", "mae"), ("MJ3", "rmse"), ("MJ1", "rmse")]:
        claim(
            f"Final BOUND vs B+ worse {point}/{metric}",
            points.loc[(1168, "TiDE_BOUND", point), metric]
            > points.loc[(1168, c.B, point), metric],
        )
    for method, point, field, value in [
        ("TiDE_BOUND", "MJ3", "width90", 7.462879),
        ("TiDE_BOUND", "MJ1", "width90", 3.577016),
        ("TiDE_SPLIT", "ATU1", "width90", 94.132842),
        ("TiDE_SPLIT", "ATU5", "width90", 105.512588),
        ("TiDE_SPLIT", "ATU1", "coverage90", 1.0),
        ("TiDE_SPLIT", "ATU5", "coverage90", 1.0),
    ]:
        claim(
            f"Narrative {method}/{point}/{field}",
            round(points.loc[(1168, method, point), field], 6) == value,
        )

    files = [
        *c.ROOT.glob("docs/ootang_tide_fusion*.md"),
        c.ROOT / "README.md",
        c.ROOT / "docs/README.md",
        c.ROOT / "docs/progress.md",
        c.ROOT / "AGENTS.md",
        c.ROOT / cfg["figures"] / "README.md",
    ]
    links, unchanged_missing = [], []
    for path in files:
        prior = subprocess.run(
            ["git", "show", "HEAD:" + str(path.relative_to(c.ROOT))],
            cwd=c.ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        for link in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            if re.match(r"https?://|app://|#", link):
                continue
            target = link.split("#")[0].strip("<>")
            if not target:
                continue
            record = dict(document=str(path.relative_to(c.ROOT)), target=link)
            if not (path.parent / target).resolve().exists():
                assert "](" + link + ")" in prior, record
                unchanged_missing.append(record)
            else:
                links.append(record)
    audit = o.read_json(root / "audit_v1/receipt.json")
    qa = o.read_json(root / "figure_qa_v2/receipt.json")
    assert audit["status"] == "passed" and audit["checkpoints"] == 240
    assert audit["values"] == 188863268 and audit["formal_fit_failures"] == 0
    assert qa["status"] == "passed" and qa["figures"] == 4 and qa["panels"] == 16
    assert qa["values"] == 247192 and qa["source_max_difference"] == 0
    assert o.read_json(root / "visual_review.json")["status"] == "passed"
    interpretation = o.read_json(root / "statistical_interpretation_audit.json")
    assert interpretation["checked"] == len(interpretation["items"]) == 11
    assert not list((c.ROOT / cfg["figures"]).rglob("*.pdf"))
    initial = o.read_json(root / "initial_state.json")
    assert o.sha(c.ROOT / initial["untracked_user_file"]) == initial["user_file_sha256"]
    receipt = dict(
        status="passed",
        tables=5,
        rows=rows,
        numerical_cells=cells,
        narrative_claims=len(narrative_checks),
        narrative_checks=narrative_checks,
        local_links=len(links),
        unchanged_missing_links=unchanged_missing,
        link_records=links,
        scope="Current tables, material numerical claims, local links and completed audit artifacts; unchanged historical missing links retained",
        user_file_unchanged=True,
        source_code_sha256=o.sha(Path(__file__)),
    )
    o.write_json(root / "document_audit.json", receipt)
    print(
        json.dumps(
            {
                k: receipt[k]
                for k in [
                    "status",
                    "tables",
                    "rows",
                    "numerical_cells",
                    "narrative_claims",
                    "local_links",
                ]
            }
        )
    )


if __name__ == "__main__":
    main()
