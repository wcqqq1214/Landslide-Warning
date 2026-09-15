"""Check scientific series, actual SVG coordinates, rendered fonts and layout."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import traceback
import xml.etree.ElementTree as ET
import matplotlib.dates as mdates
import numpy as np
import pandas as pd
from scipy.stats import norm
from PIL import Image
from . import core as c

o = c.o
NS = {"s": "http://www.w3.org/2000/svg"}
XLINK = "{http://www.w3.org/1999/xlink}href"
NUMBER = r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?"


def main(attempt, version):
    cfg = c.spec()
    c.guard()
    root = c.ROOT / cfg["out"]
    folder = c.ROOT / cfg["figures"] / version
    out = root / attempt
    out.mkdir(exist_ok=False)
    o.verify_lock(root / "audit_lock.json")
    o.verify_lock(folder / "artifact_lock.json")
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

        def read(p):
            return pd.read_csv(p, float_precision="round_trip")

        summary = read(root / "analysis/phase_summary.csv").set_index(
            ["origin", "method"]
        )
        seeds = read(root / "analysis/seed_summary.csv").set_index(
            ["origin", "method", "seed"]
        )
        points = read(root / "analysis/metrics_by_point.csv").set_index(
            ["origin", "method", "point"]
        )
        arrays = o.load_npz(folder / "source_arrays.npz")
        y = o.read_labels(c.ROOT / cfg["data"], 1461)
        _, dates = o.read_forcing(c.ROOT / cfg["data"], 1461)
        dx = mdates.date2num(pd.to_datetime(dates))
        close("observed", arrays["observed"], y, 0)
        assert np.array_equal(arrays["dates"], dates)
        close("bplus", arrays["bplus_full"], o.bank(cfg)[1168]["mean"], 0)
        for name, suffix in [
            ("means", ""),
            ("seeds", "__seeds"),
            ("sigmas", "__sigma"),
        ]:
            for m, a in o.load_npz(root / f"origin_1168/{name}.npz").items():
                close(name + "/" + m, arrays[m + suffix], a, 0)
        exports = o.read_json(folder / "exports.json")
        assert exports["source_code_sha256"] == o.sha(
            c.ROOT / "code/tide_correction/figures.py"
        )
        trees = {
            v["name"]: ET.parse(folder / v["svg"]).getroot() for v in exports["figures"]
        }
        records = o.read_json(folder / "plot_records.json")
        for rec in records:
            r = rec["recipe"]
            key = rec["gid"]
            kind = r["type"]
            x = None
            yy = None
            if kind == "summary":
                x = np.arange(3)
                yy = [
                    summary.loc[(n, r["method"]), r["metric"]]
                    for n in cfg["origins"][1:]
                ]
            elif kind == "paired":
                s = r["seed"]
                x = np.arange(3) + (0 if s is None else (s - 1) * 0.045)

                def value(n, m):
                    return (
                        summary.loc[(n, m), "rmse"]
                        if s is None
                        else seeds.loc[(n, m, s), "rmse"]
                    )

                yy = [
                    value(n, r["method"]) - value(n, r["reference"])
                    for n in cfg["origins"][1:]
                ]
            elif kind == "support":
                origins = np.arange(480, 876)
                available = sorted(
                    cfg["correction"]["refit_prefixes"]
                    + cfg["correction"]["reused_oof_prefixes"]
                )
                prefixes = np.array(
                    [max(v for v in available if v <= m) for m in origins]
                )
                metric = r["metric"]
                if metric in ("eligible", "regimes"):
                    x = np.arange(4)
                    yy = []
                    for n in cfg["origins"]:
                        allowed = origins[origins + 293 <= n]
                        yy.append(
                            len(allowed)
                            if metric == "eligible"
                            else len(set(prefixes[origins + 293 <= n]))
                        )
                elif metric == "base_prefix":
                    x, yy = origins, prefixes
                elif metric == "maturity":
                    x = np.array(sorted(set(prefixes)))
                    yy = np.array(
                        [max(min(293, n - m) for m in range(432, n)) for n in x]
                    )
                else:
                    raise AssertionError(metric)
            elif kind == "point":
                x = np.arange(4)
                yy = [
                    r.get("multiplier", 1)
                    * points.loc[(1168, r["method"], p), r["metric"]]
                    for p in cfg["points"]
                ]
            elif kind in ("trajectory", "band"):
                m, p = r["method"], r["point"]
                x = dx if m in ("observed", "bplus_full") else dx[1168:]
                if kind == "trajectory":
                    data = (
                        arrays[m]
                        if r["seed"] is None
                        else arrays[m + "__seeds"][r["seed"]]
                    )
                    yy = data[:, p] - y[0, p]
                else:
                    mu = arrays[m][:, p] - y[0, p]
                    half = (
                        norm.ppf((1 + r["level"] / 100) / 2) * arrays[m + "__sigma"][p]
                    )
                    close(key + "/lower", rec["lo"], mu - half, 0)
                    close(key + "/upper", rec["hi"], mu + half, 0)
                    v = np.asarray(rec["vertices"])
                    assert len(v) == 589
                    close(key + "/polygon_lower", v[1:294, 1], mu - half, 0)
                    close(key + "/polygon_upper", v[295:588, 1], (mu + half)[::-1], 0)
            else:
                raise AssertionError(kind)
            close(key + "/x", rec["x"], x, 0)
            if yy is not None:
                close(key + "/y", rec["y"], yy, 1e-10)
            vertices = (
                np.asarray(rec["vertices"])
                if rec["kind"] == "band"
                else np.column_stack([rec["x"], rec["y"]])
            )
            assert np.all(
                (vertices[:, 0] >= rec["xlim"][0])
                & (vertices[:, 0] <= rec["xlim"][1])
                & (vertices[:, 1] >= rec["ylim"][0])
                & (vertices[:, 1] <= rec["ylim"][1])
            ), key
            tree = trees[rec["figure"]]
            group = tree.find(f".//*[@id='{key}']")
            assert group is not None
            if rec.get("marker_only"):
                xy = np.array(
                    [
                        [float(v.get("x")), float(v.get("y"))]
                        for v in group.findall(".//s:use", NS)
                    ]
                )
            else:
                if rec["kind"] == "band":
                    use = group.find(".//s:use", NS)
                    path = tree.find(f".//*[@id='{use.get(XLINK)[1:]}']")
                    offset = np.array(
                        [float(use.get("x", "0")), float(use.get("y", "0"))]
                    )
                else:
                    path = group.find("s:path", NS)
                    offset = np.zeros(2)
                assert path is not None and not re.search("[CQASTcqast]", path.get("d"))
                xy = (
                    np.array(
                        [float(v) for v in re.findall(NUMBER, path.get("d"))]
                    ).reshape(-1, 2)
                    + offset
                )
                if rec["kind"] == "band" and path.get("d").strip().lower().endswith(
                    "z"
                ):
                    xy = np.vstack([xy, xy[:1]])
            close(key + "/actual_svg", xy, rec["svg_xy"], 1.1e-6, "svg")
        notes = o.read_json(folder / "annotations.json")
        assert len(notes) == 8
        for note in notes:
            m, p = note["method"], note["point"]
            expected = [
                points.loc[(1168, m, p), "rmse"],
                points.loc[(1168, c.B, p), "rmse"],
                100 * points.loc[(1168, m, p), "coverage90"],
                points.loc[(1168, m, p), "width90"],
            ]
            close(
                note["figure"] + "/" + p + "/annotation",
                [note[k] for k in ("rmse", "rmse_bplus", "coverage90", "width90")],
                expected,
                0,
            )
            text = "".join(
                v.text or "" for v in trees[note["figure"]].findall(".//s:text", NS)
            )
            assert (
                all(f"{v:.2f}" in text for v in expected[:2])
                and f"{expected[2]:.1f}%" in text
                and f"{expected[3]:.1f}" in text
            )
        scripts = Path.home() / ".codex/skills/nature-figure/scripts"
        rendered = []
        for item in exports["figures"]:
            name = item["name"]
            tree = trees[name]
            assert tree.findall(".//s:text", NS)
            close(
                name + "/width_mm",
                float(tree.attrib["width"].removesuffix("pt")) / 72 * 25.4,
                240,
                1e-6,
                "layout",
            )
            close(
                name + "/height_mm",
                float(tree.attrib["height"].removesuffix("pt")) / 72 * 25.4,
                170,
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
                    [
                        sys.executable,
                        str(scripts / args[0]),
                        *args[1:],
                    ],
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
                str(c.ROOT / "code/tide_correction/figures.py"),
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
            source_warning_review=[
                "PNG300dpi/SVG are the frozen outputs; no TIFF required",
                "Final width240mm and glyph sizes measured after rendering",
                "Annotations below each plot area are checked in rendered output",
            ],
        )
    except Exception:
        receipt.update(status="failed", error=traceback.format_exc())
        raise
    finally:
        o.write_json(out / "checks.json", checks)
        o.write_json(out / "receipt.json", receipt)
    o.lock(out, "lock.json", list(out.glob("*")), status="passed")
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", default="figure_qa_v1")
    parser.add_argument("--figures", default="v1")
    args = parser.parse_args()
    main(args.attempt, args.figures)
