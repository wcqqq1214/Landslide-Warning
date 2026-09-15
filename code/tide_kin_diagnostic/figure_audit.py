"""Independent CSV aggregation, actual SVG geometry and rendered layout checks."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import traceback
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
from PIL import Image
from . import core as c

o = c.o
NS = {"s": "http://www.w3.org/2000/svg"}
NUMBER = r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?"


def main(attempt, version):
    cfg = c.spec()
    c.guard()
    root = c.ROOT / cfg["out"]
    folder = c.ROOT / cfg["figures"] / version
    out = root / attempt
    out.mkdir(exist_ok=False)
    for lock in [
        root / "audit_v1/lock.json",
        root / "diagnostic_v1/lock.json",
        folder / "artifact_lock.json",
    ]:
        o.verify_lock(lock)
    checks = []
    receipt = dict(
        status="running", source_max_difference=0.0, svg_max_difference_pt=0.0
    )

    def close(name, a, b, tol=1e-9, kind="source"):
        a, b = np.asarray(a, float), np.asarray(b, float)
        assert a.shape == b.shape and np.isfinite(a).all() and np.isfinite(b).all(), (
            name
        )
        diff = float(np.max(abs(a - b))) if a.size else 0.0
        assert diff <= tol, (name, diff, tol)
        if kind in ("source", "svg"):
            key = (
                "source_max_difference" if kind == "source" else "svg_max_difference_pt"
            )
            receipt[key] = max(receipt[key], diff)
        checks.append(
            dict(
                check=name,
                values=int(a.size),
                max_difference=diff,
                tolerance=tol,
                kind=kind,
            )
        )

    try:
        exports = o.read_json(folder / "exports.json")
        assert exports["source_code_sha256"] == o.sha(
            c.ROOT / "code/tide_kin_diagnostic/figures.py"
        )
        assert exports["contract_sha256"] == o.sha(
            c.ROOT / "docs/ootang_tide_kin_diagnostic_figure_contract.v1.0.md"
        )
        tables = {}
        for name, digest in exports["source_tables"].items():
            p = root / "diagnostic_v1" / (name + ".csv")
            assert o.sha(p) == digest
            tables[name] = pd.read_csv(
                p, float_precision="round_trip", dtype={"seed": str}
            )
        trees = {
            v["name"]: ET.parse(folder / v["svg"]).getroot() for v in exports["figures"]
        }
        records = o.read_json(folder / "plot_records.json")
        assert len(records) == 152
        for rec in records:
            r, key = rec["recipe"], rec["gid"]
            frame = tables[r["table"]]
            keep = np.ones(len(frame), bool)
            for col, requested in r["filters"].items():
                members = requested if isinstance(requested, list) else [requested]
                keep &= np.array([value in members for value in frame[col]])
            expected = []
            for value in r["order"]:
                v = frame[r["metric"]].to_numpy()[
                    keep & (frame[r["order_column"]].to_numpy() == value)
                ]
                assert len(v) > 0 and np.isfinite(v).all()
                expected.append(sum(v.tolist()) / len(v) * r["multiplier"])
            x = np.arange(4) if r["order_column"] == "bin" else np.array(r["order"])
            close(key + "/x", rec["x"], x, 0)
            close(key + "/y", rec["y"], expected, 1e-10)
            v = np.column_stack([rec["x"], rec["y"]])
            assert np.all(
                (v[:, 0] >= rec["xlim"][0])
                & (v[:, 0] <= rec["xlim"][1])
                & (v[:, 1] >= rec["ylim"][0])
                & (v[:, 1] <= rec["ylim"][1])
            )
            group = trees[rec["figure"]].find(f".//*[@id='{key}']")
            assert group is not None
            if rec["marker_only"]:
                xy = np.array(
                    [
                        [float(q.get("x")), float(q.get("y"))]
                        for q in group.findall(".//s:use", NS)
                    ]
                )
            else:
                path = group.find("s:path", NS)
                assert path is not None and not re.search("[CQASTcqast]", path.get("d"))
                xy = np.array(
                    [float(v) for v in re.findall(NUMBER, path.get("d"))]
                ).reshape(-1, 2)
            close(key + "/actual_svg", xy, rec["svg_xy"], 1.1e-6, "svg")
        scripts = Path.home() / ".codex/skills/nature-figure/scripts"
        rendered = []
        for item in exports["figures"]:
            name = item["name"]
            tree = trees[name]
            assert tree.findall(".//s:text", NS)
            for key, expected in [("width", 240), ("height", 170)]:
                close(
                    name + "/" + key,
                    float(tree.attrib[key].removesuffix("pt")) / 72 * 25.4,
                    expected,
                    1e-6,
                    "layout",
                )
            with Image.open(folder / item["png"]) as im:
                assert im.size == (int(240 / 25.4 * 300), int(170 / 25.4 * 300))
                close(name + "/dpi", im.info["dpi"], [300, 300], 0.01, "dpi")
            layout = o.read_json(folder / f"{name}.alignment.json")
            assert layout["verdict"] == "PASS" and layout["auditable"]
            assert o.read_json(folder / f"{name}.text_geometry.json")["passed"]
            for mode, args in [
                (
                    "text",
                    ["audit_pdf_text.py", item["qa_pdf"], "--min-pt", "5", "--json"],
                ),
                ("collision", ["audit_figure_collisions.py", item["qa_pdf"], "--json"]),
            ]:
                result = subprocess.run(
                    [sys.executable, str(scripts / args[0]), *args[1:]],
                    capture_output=True,
                    text=True,
                )
                (out / f"{name}.{mode}.json").write_text(result.stdout)
                (out / f"{name}.{mode}.stderr.txt").write_text(result.stderr)
                assert result.returncode == 0, (name, mode, result.stdout)
                rendered.append(
                    dict(figure=name, audit=mode, returncode=result.returncode)
                )
        result = subprocess.run(
            [
                sys.executable,
                str(scripts / "validate_figure.py"),
                str(c.ROOT / "code/tide_kin_diagnostic/figures.py"),
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        (out / "source_qa.json").write_text(result.stdout)
        assert json.loads(result.stdout)["summary"]["counts"]["FAIL"] == 0
        assert len(exports["figures"]) == 4 and not list(folder.glob("*.pdf"))
        receipt.update(
            status="passed",
            figures=4,
            panels=16,
            records=len(records),
            checks=len(checks),
            values=sum(v["values"] for v in checks),
            all_scientific_marks_inside_axes=True,
            rendered_audits=rendered,
            visual_review="separate manual record",
        )
    except Exception:
        receipt.update(status="failed", error=traceback.format_exc())
        raise
    finally:
        o.write_json(out / "checks.json", checks)
        o.write_json(out / "receipt.json", receipt)
        o.write_json(
            out / "source_code.json",
            dict(
                path="code/tide_kin_diagnostic/figure_audit.py",
                sha256=o.sha(Path(__file__)),
            ),
        )
    o.lock(out, "lock.json", list(out.glob("*")), status="passed")
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--attempt", default="figure_qa_v1")
    p.add_argument("--figures", default="v1")
    args = p.parse_args()
    main(args.attempt, args.figures)
