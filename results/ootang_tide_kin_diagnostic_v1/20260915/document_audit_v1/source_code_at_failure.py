"""Verify six research tables, narrative values and relevant delivery links."""

import json
from pathlib import Path
import re
import numpy as np
import pandas as pd
from . import core as c


def main():
    cfg, o = c.spec(), c.o
    c.guard()
    root = c.ROOT / cfg["out"]
    out = root / "document_audit_v1"
    out.mkdir(exist_ok=False)
    for lock in [
        root / "diagnostic_v1/lock.json",
        root / "audit_v1/lock.json",
        root / "figure_qa_v1/lock.json",
        root / "report_tables/lock.json",
        c.ROOT / cfg["figures"] / "delivery_lock.json",
    ]:
        o.verify_lock(lock)
    meta = o.read_json(root / "report_tables/source_code.json")
    assert o.sha(c.ROOT / meta["path"]) == meta["sha256"]
    for p, digest in meta["historical_references"].items():
        assert o.sha(c.ROOT / p) == digest
    checks = []

    def close(name, a, b, tol=1e-9):
        a, b = np.asarray(a, float), np.asarray(b, float)
        assert a.shape == b.shape and np.isfinite(a).all() and np.isfinite(b).all(), (
            name
        )
        diff = float(np.max(abs(a - b), initial=0))
        assert diff <= tol, (name, diff)
        checks.append(
            dict(name=name, values=int(a.size), max_difference=diff, tolerance=tol)
        )

    def read(name):
        return pd.read_csv(
            root / "diagnostic_v1" / (name + ".csv"),
            float_precision="round_trip",
            dtype={"seed": str},
        )

    points = read("issued_by_point")
    training = read("training_by_point")
    cp = read("checkpoint_forecast_by_point")
    weights = read("supervision_weights")
    tail = read("tail_sse")
    mse = read("mse_decomposition")

    def val(frame, col, **filters):
        ix = np.ones(len(frame), bool)
        for k, v in filters.items():
            ix &= frame[k].isin(v if isinstance(v, list) else [v]).to_numpy()
        a = frame.loc[ix, col].to_numpy(float)
        assert len(a) and np.isfinite(a).all()
        return float(np.sum(a) / len(a))

    expected = {}
    expected["full_rmse"] = [
        [m]
        + [
            val(points, "rmse", origin=n, method=m, seed="ensemble", bin="all")
            for n in cfg["primary_origins"]
        ]
        for m in cfg["methods"]
    ]
    expected["kin_distance"] = [
        [n]
        + [
            val(
                points,
                "rmse",
                origin=n,
                method="TiDE_KIN",
                seed="ensemble",
                bin=b["name"],
            )
            for b in cfg["bins"]
        ]
        for n in cfg["primary_origins"]
    ]
    rows = []
    for n in cfg["origins"]:
        lengths = np.minimum(293, n - np.arange(432, n))
        mass = np.maximum(lengths - 180, 0) / lengths
        row = [n, len(lengths), int((lengths == 293).sum()), float(mass.mean() * 100)]
        for s in cfg["seeds"]:
            schedule = np.load(c.folder(cfg, n, s) / "schedule.npz")["origins"][
                :400
            ].ravel()
            ls = np.minimum(293, n - schedule)
            row.append(float((np.maximum(ls - 180, 0) / ls).mean() * 100))
        rows.append(row)
    expected["supervision_support"] = rows
    rows = []
    for n in cfg["origins"]:
        row = [n]
        for f in [training, cp]:
            row += [
                val(f, "rmse", origin=n, bin="all", seed=["0", "1", "2"], step=e)
                for e in [200, 400]
            ]
        row += [
            val(cp, "rmse", origin=n, bin="all", seed="ensemble", step=e)
            for e in [200, 400]
        ]
        rows.append(row)
    expected["checkpoint_fit"] = rows
    arrays = o.load_npz(root / "diagnostic_v1/visibility_forecasts.npz")
    expected["visibility"] = []
    for n in cfg["origins"]:
        full = np.stack([arrays[f"n{n}_s{s}_d293"] for s in cfg["seeds"]]).mean(0)
        row = [n]
        for d in cfg["visibility"]:
            partial = np.stack([arrays[f"n{n}_s{s}_d{d}"] for s in cfg["seeds"]]).mean(
                0
            )
            row.append(
                float(np.sqrt(np.mean((partial[:d] - full[:d]) ** 2, axis=0)).mean())
            )
        expected["visibility"].append(row)
    expected["final_points"] = []
    for p in cfg["points"]:
        filters = dict(origin=1168, point=p, seed="ensemble")
        row = [
            p,
            val(points, "rmse", **filters, method="TiDE_KIN", bin="all"),
            val(points, "rmse", **filters, method="BPLUS_CONTINUOUS", bin="all"),
            val(points, "bias", **filters, method="TiDE_KIN", bin="h181_293"),
            val(points, "rmse", **filters, method="TiDE_KIN", bin="h181_293"),
            100 * val(points, "coverage90", **filters, method="TiDE_KIN", bin="all"),
            100
            * val(points, "coverage90", **filters, method="TiDE_KIN", bin="h181_293"),
            100 * val(tail, "tail_fraction", **filters, method="TiDE_KIN"),
        ]
        expected["final_points"].append(row)
    docpath = c.ROOT / "docs/ootang_tide_kin_diagnostic_results.v1.0.md"
    doc = docpath.read_text()
    tabmeta = o.read_json(root / "report_tables/tables.json")
    total_rows = 0
    for name, rows in expected.items():
        frame = pd.read_csv(
            root / "report_tables" / (name + ".csv"), float_precision="round_trip"
        )
        assert list(frame.columns) == tabmeta[name]["headers"] and len(frame) == len(
            rows
        )
        assert frame.iloc[:, 0].astype(str).tolist() == [str(row[0]) for row in rows]
        numeric = np.array([r[1:] for r in rows], float)
        close(name + "/source", frame.iloc[:, 1:].to_numpy(float), numeric)
        block = re.search(
            rf"<!-- table:{name} -->\n(.*?)\n<!-- endtable:{name} -->", doc, re.S
        ).group(1)
        lines = [
            [v.strip() for v in line.strip().strip("|").split("|")]
            for line in block.splitlines()
        ]
        assert lines[0] == list(frame.columns) and len(lines) == len(rows) + 2
        assert [r[0] for r in lines[2:]] == [str(r[0]) for r in rows]
        close(
            name + "/markdown",
            [[float(v) for v in row[1:]] for row in lines[2:]],
            numeric,
            5.0001e-7,
        )
        total_rows += len(rows)
    claimed = o.read_json(root / "report_tables/claims.json")
    baseline = dict(origin=792, seed="ensemble", bin="h001_030")
    expected_claims = {
        "early_kin": val(points, "rmse", **baseline, method="TiDE_KIN"),
        "early_drift": val(points, "rmse", **baseline, method="DRIFT1"),
        "uniform_tail_percent": 113 / 293 * 100,
        "mj1_972_seed_mse": val(
            mse, "mean_seed_mse", origin=972, method="TiDE_KIN", bin="all", point="MJ1"
        ),
        "mj1_972_ensemble_mse": val(
            mse, "ensemble_mse", origin=972, method="TiDE_KIN", bin="all", point="MJ1"
        ),
        "mj3_final_spread_percent": 100
        * val(
            mse,
            "ensemble_reduction_fraction",
            origin=1168,
            method="TiDE_KIN",
            bin="all",
            point="MJ3",
        ),
        "first_coverage90": 100
        * val(
            points,
            "coverage90",
            origin=792,
            method="TiDE_KIN",
            seed="ensemble",
            bin="all",
        ),
        "first_width90": val(
            points, "width90", origin=792, method="TiDE_KIN", seed="ensemble", bin="all"
        ),
    }
    assert set(claimed) == set(expected_claims)
    for k, v in expected_claims.items():
        close("claim/" + k, claimed[k], v)
        assert f"{v:.6f}" in doc, k
    # These interpretation boundaries are explicitly checked, not new scientific tests.
    for phrase in [
        "不是对实测的误差",
        "损失系数，不是最终参数梯度",
        "不按回顾成绩改用e50/e200",
        "原612没有sigma",
        "尚未实现或训练",
        "全部探索性",
        "用户/导师尚未验收",
        "先逐点开方，再平均四点",
    ]:
        assert phrase in doc, phrase
    assert len(weights) == 17580
    assert (points.loc[points.origin == 612, "probability_available"] == False).all()
    assert (
        o.read_json(c.ROOT / cfg["prior_out"] / "analysis/outcome.json")[
            "joint_bplus_pass_counts"
        ]["TiDE_KIN"]
        == 0
    )
    stats = o.read_json(root / "statistical_interpretation_audit.json")
    assert len(stats["checks"]) == 11 and stats["hypothesis_tests"] == 0
    relevant = list((c.ROOT / "docs").glob("ootang_tide_kin_diagnostic*.md")) + [
        c.ROOT / cfg["figures"] / "README.md"
    ]
    nav = [
        c.ROOT / "README.md",
        c.ROOT / "docs/README.md",
        c.ROOT / "docs/progress.md",
        c.ROOT / "AGENTS.md",
    ]
    links = []
    for p in relevant + nav:
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", p.read_text()):
            if p in nav and "kin_diagnostic" not in target:
                continue
            if "://" in target or target.startswith("#"):
                continue
            target = target.strip("<>").split("#")[0]
            assert (p.parent / target).resolve().exists(), (p, target)
            links.append(dict(file=str(p.relative_to(c.ROOT)), target=target))
    receipt = dict(
        status="passed",
        tables=len(expected),
        table_rows=total_rows,
        table_numeric_values=sum(v["numeric_cells"] for v in tabmeta.values()),
        narrative_claims=len(claimed),
        comparisons=len(checks),
        values=sum(v["values"] for v in checks),
        local_links=len(links),
        interpretation_checks=11,
        new_training=0,
        checkpoints_reselected=0,
        bootstrap_probability="unavailable",
        time_utc=o.utc(),
    )
    o.write_json(out / "checks.json", checks)
    o.write_json(out / "links.json", links)
    o.write_json(
        out / "source_code.json",
        dict(
            path="code/tide_kin_diagnostic/document_audit.py",
            sha256=o.sha(Path(__file__)),
            inspected_documents={
                str(p.relative_to(c.ROOT)): o.sha(p) for p in relevant + nav
            },
            historical_outcome_sha256=o.sha(
                c.ROOT / cfg["prior_out"] / "analysis/outcome.json"
            ),
        ),
    )
    o.write_json(out / "receipt.json", receipt)
    o.lock(out, "lock.json", list(out.glob("*")), status="passed")
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
