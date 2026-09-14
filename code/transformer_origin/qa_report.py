"""Read back the delivery tables and verify values against immutable outputs."""

import re
from urllib.parse import unquote

import numpy as np
import pandas as pd

from .core import (
    B,
    ROOT,
    guard,
    load_npz,
    read_forcing,
    read_json,
    read_labels,
    sha,
    spec,
    utc,
    verify_implementation,
    verify_lock,
    write_json,
)


def main():
    cfg = spec()
    root = ROOT / cfg["out"]
    out = root / "delivery"
    report = ROOT / "docs/ootang_transformer_origin_results.v1.0.md"
    text = report.read_text()
    tables = read_json(out / "report_tables.json")
    blocks = []
    block = []
    for line in text.splitlines() + [""]:
        if line.startswith("|"):
            block.append([c.strip() for c in line.strip("|").split("|")])
        elif block:
            blocks.append(block)
            block = []
    assert len(blocks) == len(tables) == 7
    assert len({tuple(b[0]) for b in blocks}) == 7
    for saved in tables:
        actual = next(b for b in blocks if b[0] == saved["headers"])
        assert actual[2:] == [[str(c) for c in r] for r in saved["rows"]]
    t = {r["name"]: r["rows"] for r in tables}
    s = pd.read_csv(root / "analysis/phase_summary.csv").set_index(["origin", "method"])
    p = pd.read_csv(root / "analysis/metrics_by_point.csv").set_index(
        ["origin", "method", "point"]
    )
    names = {
        B: "改进 B+",
        "DRIFT1": "DRIFT1",
        "RR_COND": "普通岭回归",
        "OLD_TRANSFORMER": "旧 Transformer 原版",
        "OLD_HALF": "旧固定半残差",
        "OLD_REG1": "旧固定 λ=1",
        "COND_ATTN": "完整历史注意力",
        "NO_OBS_ATTN": "去显式历史位移的注意力",
        "POOL_MLP": "历史均匀池化",
    }
    reverse = {v: k for k, v in names.items()}
    checked = 0

    def number(actual, expected, decimals=6):
        nonlocal checked
        assert str(actual) == f"{expected:.{decimals}f}", (actual, expected)
        checked += 1

    for row in t["rmse"]:
        m = reverse[row[0]]
        for n, v in zip(cfg["origins"][1:], row[1:]):
            number(v, s.loc[(n, m), "rmse"])
    for row in t["final"]:
        m = reverse[row[0]]
        for k, v in zip(["mae", "rmse", "crps", "interval_score90"], row[1:5]):
            number(v, s.loc[(1168, m), k])
        number(row[5].strip("%"), 100 * s.loc[(1168, m), "coverage90"], 2)
        number(row[6], s.loc[(1168, m), "width90"])
    for row in t["spatial"]:
        for m, v in zip([B] + cfg["arms"], row[1:]):
            number(v, p.loc[(1168, m, row[0]), "rmse"])
    pairs = read_json(root / "analysis/pairing.json")
    gates = read_json(root / "analysis/effect_gates.json")
    for row in t["gates"]:
        n, m = int(row[0]), reverse[row[1]]
        pair = next(
            r
            for r in pairs
            if (r["origin"], r["candidate"], r["reference"]) == (n, m, B)
        )
        assert row[2:4] == [
            "通过" if gates[str(n)][m][k] else "未过"
            for k in ["mean_pass", "probability_pass"]
        ]
        assert row[4] == f"{pair['seed_both_improve']}/3"
    for row in t["paired"]:
        n, b = int(row[0]), reverse[row[1]]
        for k, v in zip(["mae", "rmse"], row[2:4]):
            number(v, s.loc[(n, "COND_ATTN"), k] - s.loc[(n, b), k])
        pair = next(
            r
            for r in pairs
            if (r["origin"], r["candidate"], r["reference"]) == (n, "COND_ATTN", b)
        )
        assert row[4] == f"{pair['seed_both_improve']}/3"
        assert row[5:] == [
            "通过" if pair[k] else "未过" for k in ["mean_pass", "probability_pass"]
        ]
    y = read_labels(ROOT / cfg["data"], 1461)
    _, dates = read_forcing(ROOT / cfg["data"], 1461)
    direction = pd.read_csv(out / "residual_direction.csv")
    for row in direction.itertuples():
        j = cfg["points"].index(row.point)
        means = load_npz(root / f"origin_{row.origin}/means.npz")
        truth_correction = np.mean(y[row.origin : row.origin + 293, j] - means[B][:, j])
        model_correction = np.mean(means["COND_ATTN"][:, j] - means[B][:, j])
        np.testing.assert_allclose(
            [row.required_correction_mean_mm, row.predicted_correction_mean_mm],
            [truth_correction, model_correction],
            rtol=0,
            atol=1e-12,
        )
        checked += 2
        if row.origin == 1168:
            record = next(r for r in t["correction"] if r[0] == row.point)
            number(record[1], truth_correction)
            number(record[2], model_correction)
    for row, n, end in zip(t["windows"], cfg["origins"], cfg["ends"]):
        assert row[:3] == [n, dates[n - 1], f"{dates[n]}—{dates[end - 1]}"]
    # Read back the concise navigation table independently, not only the report.
    nav = (ROOT / "README.md").read_text()
    nrows = [
        line
        for line in nav.splitlines()
        if line.startswith(("| 792 |", "| 972 |", "| 1168，"))
    ]
    assert len(nrows) == 3
    for line, n in zip(nrows, cfg["origins"][1:]):
        values = [c.strip() for c in line.strip("|").split("|")][1:]
        for v, m in zip(values, [B] + cfg["arms"]):
            number(v, s.loc[(n, m), "rmse"])
    paths = [
        report,
        ROOT / "README.md",
        ROOT / "AGENTS.md",
        ROOT / "docs/README.md",
        ROOT / cfg["figures"] / "README.md",
    ]
    links = 0
    for path in paths:
        for link in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", path.read_text()):
            if "://" in link or link.startswith("#"):
                continue
            target = unquote(link.split("#")[0].strip("<>"))
            assert (path.parent / target).exists(), (path, target)
            links += 1
    # The figures, raw tables, checkpoints, sources and implementation stay frozen.
    verify_implementation(cfg)
    verify_lock(root / "scoring_lock.json")
    verify_lock(root / "training_complete.json")
    assert not list(root.glob("error_*.json"))
    write_json(
        out / "report_qa.json",
        dict(
            status="passed",
            time_utc=utc(),
            tables=7,
            rows=sum(len(t["rows"]) for t in tables),
            numerical_cells_checked=checked,
            links_checked=links,
            source_files=guard(),
            new_fits=0,
            physical_forwards=0,
            report_sha256=sha(report),
            script_sha256=sha(ROOT / "code/transformer_origin/qa_report.py"),
        ),
    )
    print(read_json(out / "report_qa.json"))


if __name__ == "__main__":
    main()
