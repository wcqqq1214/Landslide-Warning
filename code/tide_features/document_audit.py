"""Check displayed tables against locked results and verify local artifact links."""

import json
from pathlib import Path
import re
import subprocess

import pandas as pd
from . import core as c


def tables(path):
    result, current = [], []
    for line in Path(path).read_text().splitlines() + [""]:
        if line.startswith("|"):
            cells = [v.strip() for v in line.strip("|").split("|")]
            if not all(re.fullmatch(r"[-: ]+", v) for v in cells):
                current.append(cells)
        elif current:
            result.append(current)
            current = []
    return result


def main():
    cfg, o = c.spec(), c.o
    root = c.ROOT / cfg["out"]
    assert c.guard() == 751
    o.verify_lock(root / "audit_lock.json")
    o.verify_lock(c.ROOT / cfg["figures"] / "delivery_lock.json")

    def read(name):
        return pd.read_csv(root / "analysis" / name, float_precision="round_trip")

    summary = read("phase_summary.csv").set_index(["origin", "method"])
    points = read("metrics_by_point.csv").set_index(["origin", "method", "point"])
    factor = read("factorial_effects.csv")
    factor = factor[
        (factor.seed.astype(str) == "ensemble")
        & (factor.point == "average")
        & (factor.metric == "rmse")
    ].set_index(["origin", "factor"])
    result = c.ROOT / "docs/ootang_tide_features_results.v1.0.md"
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
    assert [v[0] for v in tabs[2][1:]] == list(cfg["reporting"]["factorial_effects"])
    for row in tabs[2][1:]:
        assert row[1:] == [
            f"{factor.loc[(n, row[0]), 'effect']:.6f}" for n in cfg["origins"][1:]
        ]
        cells += 3
        rows += 1
    pairs = o.read_json(root / "analysis/pairing.json")
    for row in tabs[3][1:]:
        a, b = row[:2]
        ps = [v for v in pairs if v["candidate"] == a and v["reference"] == b]
        assert len(ps) == 3
        assert row[2:] == [
            f"{sum(v[k] for v in ps)}/3"
            for k in ["mean_pass", "probability_pass", "joint_pass"]
        ]
        cells += 3
        rows += 1
    tab = tables(c.ROOT / "README.md")[0]
    assert [int(v[0]) for v in tab[1:]] == cfg["origins"][1:]
    for row in tab[1:]:
        assert row[1:] == [
            f"{summary.loc[(int(row[0]), m), 'rmse']:.6f}"
            for m in [c.B, "G_CACHED", *cfg["arms"]]
        ]
        cells += 6
        rows += 1
    files = [
        *c.ROOT.glob("docs/ootang_tide_features*.md"),
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
    qa = o.read_json(root / "figure_qa_v1/receipt.json")
    assert audit["status"] == "passed" and audit["checkpoints"] == 240
    assert audit["values"] == 187837220 and audit["formal_fit_failures"] == 0
    assert qa["status"] == "passed" and qa["figures"] == 4 and qa["panels"] == 16
    assert qa["values"] == 237192 and qa["source_max_difference"] == 0
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
        local_links=len(links),
        unchanged_missing_links=unchanged_missing,
        link_records=links,
        scope="Current numerical tables, local links and completed audit artifacts; unchanged historical missing links retained",
        user_file_unchanged=True,
        source_code_sha256=o.sha(Path(__file__)),
    )
    o.write_json(root / "document_audit.json", receipt)
    print(
        json.dumps(
            {
                k: receipt[k]
                for k in ["status", "tables", "rows", "numerical_cells", "local_links"]
            }
        )
    )


if __name__ == "__main__":
    main()
