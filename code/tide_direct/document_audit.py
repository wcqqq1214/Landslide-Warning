"""Verify displayed result tables, local links and frozen experiment receipts."""

import json
import re
import subprocess
from pathlib import Path
import pandas as pd
from . import core as c


def tables(path):
    found = []
    current = []
    for line in Path(path).read_text().splitlines() + [""]:
        if line.startswith("|"):
            cells = [s.strip() for s in line.strip("|").split("|")]
            if not all(re.fullmatch(r"[-: ]+", s) for s in cells):
                current.append(cells)
        elif current:
            found.append(current)
            current = []
    return found


def main():
    cfg = c.spec()
    o = c.o
    root = c.ROOT / cfg["out"]
    c.guard()
    o.verify_lock(root / "audit_lock.json")
    o.verify_lock(c.ROOT / cfg["figures"] / "delivery_lock.json")
    summary = pd.read_csv(
        root / "analysis/phase_summary.csv", float_precision="round_trip"
    ).set_index(["origin", "method"])
    points = pd.read_csv(
        root / "analysis/metrics_by_point.csv", float_precision="round_trip"
    ).set_index(["origin", "method", "point"])
    result = c.ROOT / "docs/ootang_tide_direct_results.v1.0.md"
    tabs = tables(result)
    assert len(tabs) == 3
    values = 0
    rows = 0
    for row in tabs[0][1:]:
        m = row[0]
        assert row[1:] == [
            f"{summary.loc[(n, m), 'rmse']:.6f}" for n in cfg["origins"][1:]
        ]
        values += 3
        rows += 1
    for row in tabs[1][1:]:
        p = row[0]
        expected = [
            f"{points.loc[(1168, m, p), 'rmse']:.6f}" for m in [c.B, *cfg["arms"]]
        ]
        expected += [
            f"{100 * points.loc[(1168, m, p), 'coverage90']:.2f}%" for m in cfg["arms"]
        ]
        assert row[1:] == expected
        values += 5
        rows += 1
    pairs = o.read_json(root / "analysis/pairing.json")
    for row in tabs[2][1:]:
        a, b = row[:2]
        ps = [p for p in pairs if p["candidate"] == a and p["reference"] == b]
        assert row[2:] == [
            f"{sum(p[k] for p in ps)}/3"
            for k in ["mean_pass", "probability_pass", "joint_pass"]
        ]
        values += 3
        rows += 1
    tab = tables(c.ROOT / "README.md")[0]
    for row in tab[1:]:
        n = int(row[0])
        assert row[1:] == [
            f"{summary.loc[(n, m), 'rmse']:.6f}"
            for m in [c.B, "G_CACHED", *cfg["arms"]]
        ]
        values += 4
        rows += 1
    files = [
        *c.ROOT.glob("docs/ootang_tide_direct*.md"),
        c.ROOT / "README.md",
        c.ROOT / "docs/README.md",
        c.ROOT / "docs/progress.md",
        c.ROOT / "AGENTS.md",
        c.ROOT / cfg["figures"] / "README.md",
    ]
    links = []
    unchanged_missing_links = []
    for p in files:
        prior = subprocess.run(
            ["git", "show", "HEAD:" + str(p.relative_to(c.ROOT))],
            capture_output=True,
            text=True,
            cwd=c.ROOT,
        ).stdout
        for link in re.findall(r"\]\(([^)]+)\)", p.read_text()):
            if re.match(r"https?://|app://|#", link):
                continue
            target = link.split("#")[0].strip("<>")
            if not target:
                continue
            resolved = (p.parent / target).resolve()
            if not resolved.exists():
                assert "](" + link + ")" in prior, (str(p), link)
                unchanged_missing_links.append(
                    {"document": str(p.relative_to(c.ROOT)), "target": link}
                )
                continue
            links.append(dict(document=str(p.relative_to(c.ROOT)), target=link))
    audit = o.read_json(root / "audit_v2/receipt.json")
    qa = o.read_json(root / "figure_qa_v1/receipt.json")
    assert (
        audit["status"] == "passed"
        and audit["checkpoints"] == 120
        and audit["values"] == 93991564
    )
    assert (
        qa["status"] == "passed"
        and qa["figures"] == 3
        and qa["panels"] == 12
        and qa["values"] == 227476
    )
    assert o.read_json(root / "visual_review.json")["status"] == "passed"
    assert not list((c.ROOT / cfg["figures"]).rglob("*.pdf"))
    o.write_json(
        root / "document_audit.json",
        dict(
            status="passed",
            tables=4,
            rows=rows,
            numerical_cells=values,
            local_links=len(links),
            scope="current result tables and local links; pre-existing missing historical links recorded without edits",
            unchanged_missing_links=unchanged_missing_links,
            link_records=links,
            source_code_sha256=o.sha(Path(__file__)),
        ),
    )
    print(
        json.dumps(
            dict(
                status="passed",
                tables=4,
                rows=rows,
                numerical_cells=values,
                local_links=len(links),
            )
        )
    )


if __name__ == "__main__":
    main()
