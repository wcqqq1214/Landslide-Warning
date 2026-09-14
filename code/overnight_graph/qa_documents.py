"""Verify reported tables and links against frozen, audited experiment artifacts."""

from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

import pandas as pd

from .core import B, ROOT, read_json, sha, spec, utc, verify_implementation, write_json


def tables(text):
    blocks, rows = [], []
    for line in text.splitlines() + [""]:
        if line.startswith("|"):
            rows.append([x.strip() for x in line.strip("|").split("|")])
        elif rows:
            blocks.append(rows[2:])
            rows = []
    return blocks


def main():
    cfg = spec()
    verify_implementation(cfg)
    root = ROOT / cfg["out"]
    scores = pd.read_csv(root / "analysis/phase_summary.csv").set_index(
        ["origin", "method"]
    )
    report = ROOT / "docs/ootang_overnight_graph_results.v1.0.md"
    text = report.read_text()
    mapping = {
        "改进 B+": B,
        "DRIFT1": "DRIFT1",
        "普通岭回归": "RR_COND",
        "旧 Transformer": "OLD_TRANSFORMER",
        "旧固定半残差": "OLD_HALF",
        "旧残差正则化 λ=1": "OLD_REG1",
        "旧起点条件化注意力": "COND_ATTN",
        "旧去显式历史位移": "NO_OBS_ATTN",
        "旧历史均匀池化": "POOL_MLP",
        "GRU 本点版": "GRU_LOCAL",
        "GRU 固定图版": "GRU_GRAPH",
    }
    blocks = tables(text)
    assert len(blocks) == 2 and len(blocks[0]) == 11 and len(blocks[1]) == 3
    values = 0
    for row in blocks[0]:
        m = mapping[row[0]]
        for cell, n in zip(row[1:], [792, 972, 1168], strict=True):
            assert cell == f"{scores.loc[(n, m), 'rmse']:.3f}", row
            values += 1
    for row in blocks[1]:
        m = mapping[row[0]]
        for cell, key in zip(
            row[1:], ["crps", "interval_score90", "coverage90", "width90"], strict=True
        ):
            value = scores.loc[(1168, m), key]
            expected = f"{value * 100:.2f}%" if key == "coverage90" else f"{value:.3f}"
            assert cell == expected, row
            values += 1
    navrows = tables((ROOT / "README.md").read_text())[0]
    assert len(navrows) == 3
    for row, n in zip(navrows, [792, 972, 1168], strict=True):
        for cell, m in zip(row[1:], [B, "GRU_LOCAL", "GRU_GRAPH"], strict=True):
            assert cell == f"{scores.loc[(n, m), 'rmse']:.6f}", row
            values += 1
    pairing = read_json(root / "analysis/pairing.json")
    paired = [
        p
        for p in pairing
        if p["candidate"] == "GRU_GRAPH" and p["reference"] == "GRU_LOCAL"
    ]
    assert [p["origin"] for p in paired] == [792, 972, 1168]
    assert [p["seed_both_improve"] for p in paired] == [1, 2, 1]
    assert not any(p["mean_pass"] or p["probability_pass"] for p in paired)
    for m in cfg["arms"]:
        ps = [p for p in pairing if p["candidate"] == m and p["reference"] == B]
        assert [p["mean_pass"] for p in ps] == [False, True, False]
        assert [p["probability_pass"] for p in ps] == [False, False, True]
        assert not any(p["joint_pass"] for p in ps)
    decision = read_json(root / "diagnostic/decision.json")
    assert not decision["triggered"] and not decision["eligible_points"]
    train = read_json(root / "base/training_complete.json")
    assert train["new_fits"] == 24 and train["updates"] == 4800
    audit = read_json(root / "independent_audit/receipt.json")
    figure = read_json(ROOT / cfg["figures"] / "v2/delivery_qa.json")
    assert audit["status"] == figure["status"] == "passed"
    for value in [11110304, 70346]:
        assert str(value) in text
    assert audit["values_checked"] == 11110304 and figure["values_checked"] == 70346
    point = pd.read_csv(root / "analysis/metrics_by_point.csv").set_index(
        ["origin", "method", "point"]
    )
    for m in cfg["arms"]:
        for p in cfg["points"]:
            assert point.loc[(1168, m, p), "rmse"] > point.loc[(1168, B, p), "rmse"]
        for control in ["DRIFT1", "RR_COND"]:
            assert not all(
                scores.loc[(1168, m), k] < scores.loc[(1168, control), k]
                for k in ["mae", "rmse"]
            )
    paths = [
        report,
        ROOT / "README.md",
        ROOT / "AGENTS.md",
        ROOT / "docs/README.md",
        ROOT / "docs/ootang_overnight_graph_validation.v1.0.md",
        ROOT / cfg["figures"] / "README.md",
    ]
    links, remote = [], []
    for path in paths:
        for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text()):
            target = link.strip("<>")
            if urlsplit(target).scheme:
                remote.append(target)
                continue
            local = unquote(target.split("#", 1)[0])
            resolved = (path.parent / local).resolve() if local else path
            assert resolved.exists(), (str(path), link)
            links.append(
                dict(
                    document=str(path.relative_to(ROOT)),
                    target=str(resolved.relative_to(ROOT)),
                )
            )
    receipt = dict(
        status="passed",
        time_utc=utc(),
        tables=3,
        rows=17,
        numerical_values=values,
        local_links_checked=len(links),
        remote_links_not_refetched=remote,
        source_and_implementation_guard="passed",
        assertions="tables, point error direction, all gates, seed agreement, counts and trigger",
        document_sha256={str(p.relative_to(ROOT)): sha(p) for p in paths},
        script_sha256=sha(Path(__file__)),
        new_training=0,
    )
    write_json(root / "document_qa.json", receipt)
    print(receipt)


if __name__ == "__main__":
    main()
