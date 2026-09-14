"""Re-read report tables, derived comparisons, documentation links and navigation."""

import re
from pathlib import Path

import numpy as np
import pandas as pd

from .core import (
    B,
    OLD,
    REG,
    HALF,
    ROOT,
    guard_sources,
    read_json,
    sha,
    spec,
    utc,
    write_json,
)


def main():
    cfg = spec()
    root = ROOT / cfg["out"]
    analysis = root / "analysis"
    report = ROOT / "docs/ootang_transformer_calibration_results.v1.0.md"
    record = read_json(analysis / "report_tables.json")
    body = report.read_text()
    assert record["report_sha256"] == sha(report)
    frames = {}
    csv_numeric_cells = 0
    for name, h in record["source_files"].items():
        path = ROOT / name
        assert sha(path) == h
        frame = pd.read_csv(path)
        original = pd.read_csv(root / "verification_v1" / path.name)
        pd.testing.assert_frame_equal(frame, original, check_exact=True)
        frames[name] = frame
        csv_numeric_cells += frame.select_dtypes(include="number").size
    values = []
    report_rows = 0
    for table in record["tables"]:
        found = re.search(
            r"<!-- table:"
            + table["id"]
            + r" -->\n(.*?)\n<!-- endtable:"
            + table["id"]
            + r" -->",
            body,
            re.S,
        )
        assert found, table["id"]
        lines = [line for line in found.group(1).splitlines() if line.startswith("|")]
        parsed = [[v.strip() for v in line.strip("|").split("|")] for line in lines]
        assert parsed[0] == table["headers"]
        assert parsed[2:] == table["rows"], table["id"]
        report_rows += len(parsed) - 2
        if table["id"] == "seed_pairing":
            continue
        start = 2 if table["id"].startswith("probability_") else 1
        values.extend(v for row in parsed[2:] for v in row[start:])
    assert len(values) == len(record["numeric_cells"])
    for text, cell in zip(values, record["numeric_cells"]):
        row = frames[cell["file"]]
        for k, v in cell["filters"].items():
            row = row[row[k] == v]
        assert len(row) == 1
        expected = float(row.iloc[0][cell["metric"]]) * cell["multiplier"]
        assert text == f"{expected:.{cell['precision']}f}", (text, cell)
    summary = pd.read_csv(analysis / "phase_summary.csv").set_index(
        ["phase", "model", "rule"]
    )
    points = pd.read_csv(analysis / "metrics_by_point.csv").set_index(
        ["phase", "model", "rule", "point"]
    )
    seeds = pd.read_csv(analysis / "seed_summary.csv").set_index(
        ["phase", "model", "rule", "seed"]
    )
    pairs = pd.read_csv(analysis / "shrinkage_pairing.csv").set_index(["phase", "pair"])
    outcome = read_json(analysis / "outcome.json")
    derived = 0
    for k in ("mae", "rmse"):
        a, b, c = [
            summary.loc[("development", m, "LAST90"), k] for m in (OLD, HALF, REG)
        ]
        np.testing.assert_allclose(
            outcome["development_half_share_of_reg_average_improvement"][k],
            (a - b) / (a - c),
            atol=1e-12,
            rtol=0,
        )
        derived += 1
    for m in (B, REG, HALF):
        for k in ("crps", "interval_score90", "width90"):
            ratio = (
                1
                - summary.loc[("final_exploratory", m, "DIST90"), k]
                / summary.loc[("final_exploratory", m, "LAST90"), k]
            )
            np.testing.assert_allclose(
                outcome["final_distance_changes"][m][k], ratio, atol=1e-12, rtol=0
            )
            derived += 1
    for phase in ("development", "final_exploratory"):
        for a, b, pair in (
            (REG, HALF, "reg_vs_half"),
            (REG, OLD, "reg_vs_original"),
            (HALF, OLD, "half_vs_original"),
        ):
            expected = []
            for seed in cfg["seeds"]:
                valid = all(
                    seeds.loc[(phase, a, "LAST90", seed), k]
                    < seeds.loc[(phase, b, "LAST90", seed), k]
                    for k in ("mae", "rmse")
                )
                expected.append(valid)
                assert bool(pairs.loc[(phase, pair), f"seed{seed}"]) == valid
            assert pairs.loc[(phase, pair), "seeds_both_mean_improve"] == sum(expected)
    for m in (REG, HALF):
        for phase in ("development", "final_exploratory"):
            for rule in cfg["rules"]:
                for k in ("mae", "rmse"):
                    assert (
                        summary.loc[(phase, m, rule), k]
                        < summary.loc[(phase, "RR_COND", rule), k]
                    )
                for k in ("crps", "interval_score90"):
                    assert (
                        (
                            summary.loc[(phase, m, rule), k]
                            > summary.loc[(phase, "RR_COND", rule), k]
                        )
                        if phase == "development"
                        else (
                            summary.loc[(phase, m, rule), k]
                            < summary.loc[(phase, "RR_COND", rule), k]
                        )
                    )
                effect = read_json(root / phase / "effect_gates.json")[m + "__" + rule]
                assert not effect["vs_bplus_same_rule"]["joint_pass"]
            for k in ("mae", "rmse"):
                if phase == "development":
                    assert (
                        summary.loc[(phase, m, "LAST90"), k]
                        > summary.loc[(phase, "DRIFT1", "LAST90"), k]
                    )
                else:
                    assert (
                        summary.loc[(phase, m, "LAST90"), k]
                        < summary.loc[(phase, "DRIFT1", "LAST90"), k]
                    )
    for m in (REG, HALF):
        assert (
            points.loc[("final_exploratory", m, "DIST90", "MJ3"), "rmse"]
            > points.loc[("final_exploratory", B, "DIST90", "MJ3"), "rmse"]
        )
    assert (
        points.loc[("final_exploratory", HALF, "DIST90", "MJ1"), "mae"]
        > points.loc[("final_exploratory", B, "DIST90", "MJ1"), "mae"]
    )
    assert points.loc[("final_exploratory", B, "DIST90", "MJ3"), "coverage90"] < 0.8
    selection = read_json(root / "selection.json")
    assert outcome["mean_selection"] == selection["mean_winner"] == "DRIFT1"
    assert (
        outcome["probability_selection"]
        == selection["probability_winner"]
        == "DRIFT1__LAST90"
    )
    assert (
        outcome["search_trigger"] is False
        and selection["search_recommendation"] is False
    )
    assert outcome["final_reg_vs_half_within_one_percent"] is True
    # The seed table must also be grounded independently, not only match its rendered manifest.
    seed_table = next(t for t in record["tables"] if t["id"] == "seed_pairing")
    expected_rows = []
    for phase in ("development", "final_exploratory"):
        for a, b, label in (
            (HALF, OLD, "HALF 优于原版"),
            (REG, OLD, "REG1 优于原版"),
            (REG, HALF, "REG1 优于 HALF"),
        ):
            winners = [
                s
                for s in cfg["seeds"]
                if all(
                    seeds.loc[(phase, a, "LAST90", s), k]
                    < seeds.loc[(phase, b, "LAST90", s), k]
                    for k in ("mae", "rmse")
                )
            ]
            expected_rows.append(
                [
                    "开发" if phase == "development" else "最终（探索）",
                    label,
                    f"{len(winners)}/3",
                    ", ".join(map(str, winners)) or "无",
                ]
            )
    assert seed_table["rows"] == expected_rows
    readme = (ROOT / "README.md").read_text()
    labels = {
        B: "改进 B+",
        "DRIFT1": "DRIFT1",
        "RR_COND": "普通岭回归",
        OLD: "Transformer 残差原版",
        REG: "Transformer 残差正则版 REG1",
        HALF: "Transformer 半残差 HALF",
    }
    for m, label in labels.items():
        row = next(
            line for line in readme.splitlines() if line.startswith("| " + label + " |")
        )
        columns = [v.strip() for v in row.strip("|").split("|")]
        assert columns[1:] == [
            f"{summary.loc[(ph, m, 'LAST90'), 'rmse']:.4f}"
            for ph in ("development", "final_exploratory")
        ]
    paths = [
        report,
        ROOT / "docs/ootang_transformer_calibration_validation.v1.0.md",
        ROOT / "README.md",
        ROOT / "AGENTS.md",
        ROOT / "docs/README.md",
        ROOT / cfg["figures"] / "README.md",
    ]
    link_count = 0
    for path in paths:
        for raw in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", path.read_text()):
            if raw.startswith(("https://", "http://", "#")):
                continue
            raw = raw.split("#")[0]
            target = Path(raw) if raw.startswith("/") else path.parent / raw
            if target == analysis / "report_qa.json":
                continue
            assert target.exists(), (path, target)
            link_count += 1
    qa = dict(
        status="passed",
        time_utc=utc(),
        tables=len(record["tables"]),
        report_rows=report_rows,
        report_numeric_cells=len(values),
        analysis_csv_numeric_cells=csv_numeric_cells,
        derived_ratios=derived,
        seed_rows_independently_checked=6,
        readme_rmse_values=12,
        current_links_checked=link_count,
        source_files_checked=guard_sources(),
        report_sha256=sha(report),
        new_training=0,
        scientific_effect_separate_from_execution=True,
    )
    write_json(analysis / "report_qa.json", qa)
    print(qa)


if __name__ == "__main__":
    main()
