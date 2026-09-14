"""Validate actual SVG curves, bands, plot records, fonts and raster exports."""

import json
import re
import xml.etree.ElementTree as ET

from matplotlib import font_manager, ft2font
import numpy as np
import pandas as pd
from PIL import Image
from scipy.stats import norm

from transformer_regularization.qa_figures import NS, affine, vertices, NUMBER
from .core import (
    ALPHAS,
    B,
    ROOT,
    load_npz,
    read_forcing,
    read_json,
    read_labels,
    sha,
    spec,
    utc,
    write_json,
)


def main():
    cfg = spec()
    root = ROOT / cfg["out"]
    out = ROOT / cfg["figures"] / "v1"
    source = load_npz(out / "source_arrays.npz")
    manifest = read_json(out / "manifest.json")
    truth = read_labels(ROOT / cfg["data"], 1461)
    _, dates = read_forcing(ROOT / cfg["data"], 1461)
    np.testing.assert_array_equal(source["observed"], truth)
    np.testing.assert_array_equal(source["dates"], dates)
    np.testing.assert_array_equal(source["y0"], truth[0])
    for phase in ["alpha", "lambda"]:
        for k, v in load_npz(root / f"origin_1168/{phase}_means.npz").items():
            np.testing.assert_array_equal(source[k], v)
        for k, v in load_npz(root / f"origin_1168/{phase}_sigmas.npz").items():
            np.testing.assert_array_equal(source[k + "__sigma"], v)
    for p, h in manifest["files"].items():
        assert sha(out / p) == h
    assert manifest["source_script_sha256"] == sha(
        ROOT / "code/transformer_temporal/figures.py"
    )
    table = pd.read_csv(root / "analysis/metrics_by_point.csv").set_index(
        ["origin", "method", "point"]
    )
    summary = pd.read_csv(root / "analysis/phase_summary.csv").set_index(
        ["origin", "method"]
    )
    seeds = pd.read_csv(root / "analysis/seed_summary.csv").set_index(
        ["origin", "method", "seed"]
    )
    nums = pd.read_csv(out / "figure_numbers.csv").set_index(["figure", "point"])
    charset = ft2font.FT2Font(
        font_manager.findfont("Arial Unicode MS", fallback_to_default=False)
    ).get_charmap()
    checked = 0
    max_difference = 0.0
    exports = []
    for export in manifest["exports"]:
        name = export["figure"]
        svg = ET.parse(out / (name + ".svg")).getroot()
        text = "\n".join("".join(t.itertext()) for t in svg.findall(".//s:text", NS))
        sizes = []
        for t in svg.findall(".//s:text", NS):
            match = re.search(r"font-size:\s*([\d.]+)px", t.get("style", ""))
            if match:
                sizes.append(float(match[1]))
        assert sizes and min(sizes) >= 5
        missing = {c for c in text if not c.isspace() and ord(c) not in charset}
        assert not missing, missing
        assert abs(float(svg.get("width").replace("pt", "")) * 25.4 / 72 - 240) < 1e-5
        assert abs(float(svg.get("height").replace("pt", "")) * 25.4 / 72 - 170) < 1e-5
        assert read_json(out / (name + ".alignment.json"))["verdict"] == "PASS"
        assert read_json(out / (name + ".text_geometry.json"))["passed"]
        with Image.open(out / (name + ".png")) as im:
            assert (
                abs(im.width - 240 / 25.4 * 300) <= 1
                and abs(im.height - 170 / 25.4 * 300) <= 1
            )
            assert all(abs(x - 300) < 0.01 for x in im.info["dpi"])
        if export["kind"] == "trajectory":
            method = export["method"]
            for p, point in enumerate(cfg["points"]):
                observed = truth[:, p] - truth[0, p]
                v = vertices(svg.find(f'.//*[@id="observed_{point}"]'))
                assert v.shape == (1461, 2)
                xs, xb = affine(np.arange(1461), v[:, 0])
                ys, yb = affine(observed, v[:, 1])
                assert xs > 0 and ys < 0
                for gid, raw in [
                    ("observed", observed),
                    ("bplus", source[B][:, p] - truth[0, p]),
                    ("model", source[method][:, p] - truth[0, p]),
                ]:
                    v = vertices(svg.find(f'.//*[@id="{gid}_{point}"]'))
                    assert v.shape == (1461, 2)
                    np.testing.assert_allclose(
                        v[:, 0], xs * np.arange(1461) + xb, atol=2e-6, rtol=0
                    )
                    delta = float(np.max(abs((v[:, 1] - yb) / ys - raw)))
                    assert delta < 1e-4, (name, point, delta)
                    max_difference = max(max_difference, delta)
                    checked += len(raw)
                mu = source[method][1168:, p] - truth[0, p]
                sd = source[method + "__sigma"][p]
                limits = nums.loc[(name, point), ["ylim_low", "ylim_high"]].to_numpy(
                    float
                )
                for level in [80, 95]:
                    band = vertices(svg.find(f'.//*[@id="band_{point}_{level}"]'))
                    day = np.rint((band[:, 0] - xb) / xs).astype(int)
                    assert set(day) == set(range(1168, 1461))
                    raw = (band[:, 1] - yb) / ys
                    z = norm.ppf((1 + level / 100) / 2)
                    lo, hi = mu - z * sd, mu + z * sd
                    for j, d in enumerate(range(1168, 1461)):
                        delta = max(
                            abs(raw[day == d].min() - lo[j]),
                            abs(raw[day == d].max() - hi[j]),
                        )
                        assert delta < 1e-4, (name, point, level, d, delta)
                        max_difference = max(max_difference, float(delta))
                        checked += 2
                    assert (
                        int(((lo < limits[0]) | (hi > limits[1])).sum())
                        == nums.loc[(name, point), f"band{level}_outside_days"]
                    )
                for k, model in [("rmse", method), ("bplus_rmse", B)]:
                    assert (
                        abs(
                            nums.loc[(name, point), k]
                            - table.loc[(1168, model, point), "rmse"]
                        )
                        < 1e-9
                    )
                assert f"模型 {table.loc[(1168, method, point), 'rmse']:.2f} mm" in text
                assert f"B+ {table.loc[(1168, B, point), 'rmse']:.2f} mm" in text
                axis = svg.find(f'.//*[@id="axes_{p + 1}"]')
                for kind in ["x", "y"]:
                    ticks = [
                        g
                        for g in axis.findall(".//s:g", NS)
                        if g.get("id", "").startswith(kind + "tick_")
                    ]
                    assert ticks
                    for tick in ticks:
                        assert any(
                            "#d9dde1" in path.get("style", "")
                            for path in tick.findall(".//s:path", NS)
                        )
        else:
            ns = cfg["origins"][1:]
            selected = read_json(out / "selected_parameters.json")
            for record in read_json(out / "temporal_plot_records.json"):
                gid = record["gid"]
                group = svg.find(f'.//*[@id="{gid}"]')
                if record["kind"] == "markers":
                    uses = group.findall(".//s:use", NS)
                    actual = np.array(
                        [[float(u.get("x")), float(u.get("y"))] for u in uses]
                    )
                else:
                    path = group.find(".//s:path", NS)
                    actual = np.array(
                        re.findall(
                            r"[ML]\s*(" + NUMBER + r")\s+(" + NUMBER + r")",
                            path.get("d"),
                        ),
                        float,
                    )
                np.testing.assert_allclose(
                    actual, np.array(record["svg_xy"]), atol=2e-6, rtol=0
                )
                if gid == "chosen_alpha":
                    expected = [ALPHAS[selected[str(n)]["alpha"]] for n in ns]
                elif gid == "chosen_lambda":
                    expected = [cfg["lambdas"][selected[str(n)]["lambda"]] for n in ns]
                elif gid.startswith("ratio_"):
                    m = gid[len("ratio_") :]
                    expected = [
                        summary.loc[(n, m), "rmse"] / summary.loc[(n, B), "rmse"]
                        for n in ns
                    ]
                elif gid.startswith("seed_"):
                    s = int(gid[-1])
                    expected = [
                        100
                        * (
                            seeds.loc[(n, "LAMBDA_SELECTED", s), "rmse"]
                            / seeds.loc[(n, "ALPHA_SELECTED", s), "rmse"]
                            - 1
                        )
                        for n in ns
                    ]
                else:
                    p = gid[len("coverage_") :]
                    expected = [
                        100 * table.loc[(n, "LAMBDA_SELECTED", p), "coverage90"]
                        for n in ns
                    ]
                np.testing.assert_allclose(record["y"], expected, atol=1e-10, rtol=0)
                checked += len(expected)
        exports.append(
            dict(figure=name, min_font_pt=min(sizes), alignment="PASS", geometry="PASS")
        )
    write_json(
        out / "delivery_qa.json",
        dict(
            status="passed",
            time_utc=utc(),
            figures=len(exports),
            panels=20,
            values_checked=checked,
            max_svg_quantization_difference_mm=max_difference,
            exports=exports,
            pdf_audits="not performed: user requested no PDF",
            visual_inspection="pending",
        ),
    )
    print(json.dumps(read_json(out / "delivery_qa.json"), ensure_ascii=False))


if __name__ == "__main__":
    main()
