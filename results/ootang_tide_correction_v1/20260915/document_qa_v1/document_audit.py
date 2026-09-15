"""Check displayed numbers and material interpretations against locked results."""

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
    assert c.guard() == 2216
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
    support = o.read_json(root / "mature_support.json")
    result = c.ROOT / "docs/ootang_tide_correction_results.v1.0.md"
    tabs = tables(result)
    assert len(tabs) == 6 and "@@" not in result.read_text()
    cells, rows = 0, 0
    assert [v[0] for v in tabs[0][1:]] == cfg["controls"] + cfg["arms"]
    for row in tabs[0][1:]:
        assert row[1:] == [
            f"{summary.loc[(n, row[0]), 'rmse']:.6f}" for n in cfg["origins"][1:]
        ]
        cells += 3
        rows += 1
    assert len(tabs[1][1:]) == 4
    for row, v in zip(tabs[1][1:], support):
        assert row == [
            str(v["origin"]),
            str(v["eligible_origins"]),
            "无" if v["first"] is None else f"{v['first']}—{v['last']}",
            str(v["base_regimes"]),
        ]
        cells += 3 + 2 * (v["first"] is not None)
        rows += 1
    assert [v[0] for v in tabs[2][1:]] == cfg["points"]
    for row in tabs[2][1:]:
        point = row[0]
        expected = [
            f"{points.loc[(1168, m, point), 'rmse']:.6f}" for m in [c.B, *cfg["arms"]]
        ] + [
            f"{100 * points.loc[(1168, m, point), 'coverage90']:.2f}%"
            for m in cfg["new_arms"]
        ]
        assert row[1:] == expected
        cells += 6
        rows += 1
    assert len(tabs[3][1:]) == 9
    for row, (n, m) in zip(
        tabs[3][1:],
        [(n, m) for n in cfg["origins"][1:] for m in cfg["arms"]],
    ):
        assert row == [
            str(n),
            m,
            f"{summary.loc[(n, m), 'crps']:.6f}",
            f"{summary.loc[(n, m), 'interval_score90']:.6f}",
            f"{100 * summary.loc[(n, m), 'coverage90']:.2f}%",
            f"{summary.loc[(n, m), 'width90']:.6f}",
        ]
        cells += 5
        rows += 1
    edges = [(m, c.B) for m in cfg["arms"]] + [
        tuple(v) for v in cfg["reporting"]["paired_edges"]
    ]
    assert [tuple(v[:2]) for v in tabs[4][1:]] == edges
    for row in tabs[4][1:]:
        a, b = row[:2]
        ps = [v for v in pairs if v["candidate"] == a and v["reference"] == b]
        assert len(ps) == 3
        assert row[2:] == [
            f"{sum(v[k] for v in ps)}/3"
            for k in ["mean_pass", "probability_pass", "joint_pass"]
        ]
        cells += 3
        rows += 1
    assert [v[0] for v in tabs[5][1:]] == cfg["points"]
    for row in tabs[5][1:]:
        point = row[0]
        assert row[1:] == [
            f"{comps.loc[(1168, 'TiDE_CAL', 'ensemble', point), 'cap_mm']:.6f}"
        ] + [
            f"{comps.loc[(1168, m, 'ensemble', point), 'hydro_abs_max']:.6f}"
            for m in cfg["new_arms"]
        ]
        cells += 3
        rows += 1
    tab = tables(c.ROOT / "README.md")[0]
    assert [int(v[0]) for v in tab[1:]] == cfg["origins"][1:]
    for row in tab[1:]:
        assert row[1:] == [
            f"{summary.loc[(int(row[0]), m), 'rmse']:.6f}" for m in [c.B, *cfg["arms"]]
        ]
        cells += 5
        rows += 1

    narrative = []

    def claim(name, passed):
        assert passed, name
        narrative.append(dict(claim=name, passed=True))

    def pair(n, a, b):
        return next(
            v
            for v in pairs
            if v["origin"] == n and v["candidate"] == a and v["reference"] == b
        )

    for n in cfg["origins"][1:]:
        for m in cfg["new_arms"]:
            for metric in ["mae", "rmse", "crps"]:
                claim(
                    f"{m} worseKIN {n}/{metric}",
                    summary.loc[(n, m), metric] > summary.loc[(n, "TiDE_KIN"), metric],
                )
            claim(
                f"{m} RR mean gate only972/{n}",
                pair(n, m, "RR_COND")["mean_pass"] == (n == 972),
            )
            claim(f"{m} DRIFT1 gate never/{n}", not pair(n, m, "DRIFT1")["mean_pass"])
        for metric in ["mae", "rmse", "crps"]:
            claim(
                f"HCAL CAL ensemble direction {n}/{metric}",
                bool(
                    summary.loc[(n, "TiDE_HCAL"), metric]
                    < summary.loc[(n, "TiDE_CAL"), metric]
                )
                == (n == 1168),
            )
    for a, b, expected in [
        ("TiDE_HCAL", "TiDE_CAL", [3, 0, 0]),
        ("TiDE_CAL", "TiDE_KIN", [1, 0, 0]),
        ("TiDE_HCAL", "TiDE_KIN", [1, 0, 0]),
    ]:
        claim(
            a + " vs " + b + " same seeds",
            [pair(n, a, b)["seed_both_improve"] for n in cfg["origins"][1:]]
            == expected,
        )
    for n, delta in zip(cfg["origins"][1:], [0.196788, 1.022692, -3.591472]):
        claim(
            f"paired RMSE delta {n}",
            round(
                summary.loc[(n, "TiDE_HCAL"), "rmse"]
                - summary.loc[(n, "TiDE_CAL"), "rmse"],
                6,
            )
            == delta,
        )
    for m, improving in [("TiDE_CAL", "MJ3"), ("TiDE_HCAL", "ATU5")]:
        for pt in cfg["points"]:
            for metric in ["mae", "rmse"]:
                claim(
                    f"final {m} B+ {pt}/{metric}",
                    bool(
                        points.loc[(1168, m, pt), metric]
                        < points.loc[(1168, c.B, pt), metric]
                    )
                    == (pt == improving),
                )
    for pt in cfg["points"]:
        for metric in ["mae", "rmse"]:
            claim(
                f"final HCAL worseKIN {pt}/{metric}",
                points.loc[(1168, "TiDE_HCAL", pt), metric]
                > points.loc[(1168, "TiDE_KIN", pt), metric],
            )
    claim(
        "final CAL B+ probability",
        pair(1168, "TiDE_CAL", c.B)["probability_pass"]
        and not pair(1168, "TiDE_CAL", c.B)["mean_pass"],
    )
    claim(
        "final HCAL MJ3 coverage",
        round(100 * points.loc[(1168, "TiDE_HCAL", "MJ3"), "coverage90"], 2) == 62.12,
    )
    claim(
        "final HCAL GRU mean split",
        summary.loc[(1168, "TiDE_HCAL"), "rmse"]
        < summary.loc[(1168, "G_CACHED"), "rmse"]
        and summary.loc[(1168, "TiDE_HCAL"), "mae"]
        > summary.loc[(1168, "G_CACHED"), "mae"]
        and pair(1168, "TiDE_HCAL", "G_CACHED")["seed_both_improve"] == 0,
    )
    for m, val in zip(cfg["arms"], [12.237801, 63.105379, 68.260132]):
        claim("final width " + m, round(summary.loc[(1168, m), "width90"], 6) == val)
    claim(
        "oldest base supervision",
        480 - cfg["correction"]["base_fit_first_origin"] == 48,
    )
    outcome = o.read_json(root / "analysis/outcome.json")
    claim(
        "stable HCAL rejected", not any(outcome["stable_hcal_vs_references"].values())
    )
    claim(
        "all B+ joint0",
        all(v == 0 for v in outcome["joint_bplus_pass_counts"].values()),
    )

    files = [
        *c.ROOT.glob("docs/ootang_tide_correction*.md"),
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
    audit = o.read_json(root / "audit_v2/receipt.json")
    qa = o.read_json(root / "figure_qa_v1/receipt.json")
    assert audit["status"] == "passed" and audit["checkpoints"] == 261
    assert audit["values"] == 89997623 and audit["formal_fit_failures"] == 0
    assert qa["status"] == "passed" and qa["figures"] == 4 and qa["panels"] == 16
    assert qa["values"] == 257388 and qa["source_max_difference"] == 0
    assert o.read_json(root / "visual_review.json")["status"] == "passed"
    interpretation = o.read_json(root / "statistical_interpretation_audit.json")
    assert interpretation["checked"] == len(interpretation["items"]) == 11
    assert not list((c.ROOT / cfg["figures"]).rglob("*.pdf"))
    initial = o.read_json(root / "initial_state.json")
    assert o.sha(c.ROOT / initial["untracked_user_file"]) == initial["user_file_sha256"]
    receipt = dict(
        status="passed",
        tables=7,
        rows=rows,
        numerical_cells=cells,
        narrative_claims=len(narrative),
        narrative_checks=narrative,
        local_links=len(links),
        unchanged_missing_links=unchanged_missing,
        link_records=links,
        user_file_unchanged=True,
        source_code_sha256=o.sha(Path(__file__)),
        scope="Displayed current tables, material numerical claims, local links and completed audit artifacts; unchanged historical missing links retained",
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
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
