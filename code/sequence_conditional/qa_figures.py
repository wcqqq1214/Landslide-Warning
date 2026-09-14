"""Check actual SVG vertices, labels, exported data, fonts, and PNG dimensions."""

import json
import re
import xml.etree.ElementTree as ET

from matplotlib import font_manager, ft2font
import numpy as np
import pandas as pd
from PIL import Image
from scipy.special import ndtri

from .core import ROOT, load_npz, read_json, read_labels, sha, spec, utc, write_json

NS = {"s": "http://www.w3.org/2000/svg"}
NUMBER = r"[-+]?(?:\d*\.)?\d+(?:[eE][-+]?\d+)?"


def vertices(group):
    p = group.find(".//s:path", NS)
    v = np.array(
        re.findall(r"[ML]\s*(" + NUMBER + r")\s+(" + NUMBER + r")", p.attrib["d"]),
        float,
    )
    use = group.find(".//s:use", NS)
    if use is not None:
        v += np.array([float(use.get("x", "0")), float(use.get("y", "0"))])
    return v


def affine(raw, rendered):
    a = ((raw - raw.mean()) * (rendered - rendered.mean())).sum() / (
        (raw - raw.mean()) ** 2
    ).sum()
    b = rendered.mean() - a * raw.mean()
    return a, b


def main():
    cfg = spec()
    root = ROOT / cfg["out"]
    out = ROOT / cfg["figures"] / "v1"
    source = load_npz(out / "source_arrays.npz")
    issue = load_npz(root / "final_exploratory/issued_distribution.npz")
    metrics = pd.read_csv(root / "final_exploratory/metrics_by_point.csv").set_index(
        ["model", "point"]
    )
    numbers = pd.read_csv(out / "figure_numbers.csv").set_index(["figure", "point"])
    truth = read_labels(ROOT / cfg["data"], 1461)
    np.testing.assert_array_equal(source["observed"], truth)
    np.testing.assert_array_equal(source["y0"], truth[0])
    for k, v in issue.items():
        if k != "dates":
            np.testing.assert_array_equal(source[k], v)
    manifest = read_json(out / "manifest.json")
    for name, digest in manifest["files"].items():
        assert sha(out / name) == digest, name
    assert (
        sha(ROOT / "code/sequence_conditional/figures.py")
        == manifest["source_script_sha256"]
    )
    font = font_manager.findfont("Arial Unicode MS", fallback_to_default=False)
    charset = ft2font.FT2Font(font).get_charmap()
    panel_rows = []
    checked = 0
    max_svg_difference_mm = 0.0
    exports = []
    for name in [*cfg["arms"], "all_methods_forecast"]:
        svg = ET.parse(out / (name + ".svg")).getroot()
        width = float(svg.get("width").replace("pt", "")) * 25.4 / 72
        height = float(svg.get("height").replace("pt", "")) * 25.4 / 72
        assert abs(width - 240) < 1e-5 and abs(height - 170) < 1e-5
        text = "\n".join("".join(t.itertext()) for t in svg.findall(".//s:text", NS))
        for word in ["ATU1", "ATU5", "MJ3", "MJ1", "293", "实测", "库水位"]:
            assert word in text, word
        missing = sorted({c for c in text if not c.isspace() and ord(c) not in charset})
        assert not missing, missing
        for t in svg.findall(".//s:text", NS):
            match = re.search(r"font-size:\s*([\d.]+)px", t.get("style", ""))
            if match:
                assert float(match[1]) >= 7.6
        assert read_json(out / (name + ".alignment.json"))["verdict"] == "PASS"
        assert read_json(out / (name + ".text_geometry.json"))["passed"]
        with Image.open(out / (name + ".png")) as image:
            assert abs(image.width - 240 / 25.4 * 300) <= 1
            assert abs(image.height - 170 / 25.4 * 300) <= 1
            assert all(abs(d - 300) < 0.01 for d in image.info["dpi"])
            dimensions = list(image.size)
        for p, point in enumerate(cfg["points"]):
            if name in cfg["arms"]:
                ref = vertices(svg.find(f'.//*[@id="observed_{point}"]'))
                raw = truth[:, p] - truth[0, p]
                expected_days = 1461
                line_ids = {
                    f"bplus_{point}": source["BPLUS_CONTINUOUS"][:, p] - truth[0, p],
                    f"model_{name}_{point}": source[name][:, p] - truth[0, p],
                }
            else:
                ref = vertices(
                    svg.find(f'.//*[@id="comparison_BPLUS_CONTINUOUS_{point}"]')
                )
                raw = issue["BPLUS_CONTINUOUS__mean"][:, p] - truth[0, p]
                expected_days = 293
                line_ids = {
                    f"comparison_{m}_{point}": issue[m + "__mean"][:, p] - truth[0, p]
                    for m in cfg["methods"]
                }
                line_ids[f"comparison_observed_{point}"] = truth[1168:, p] - truth[0, p]
            assert ref.shape == (expected_days, 2)
            xscale, xshift = affine(np.arange(expected_days), ref[:, 0])
            yscale, yshift = affine(raw, ref[:, 1])
            assert xscale > 0 and yscale < 0
            max_delta = float(abs((ref[:, 1] - yshift) / yscale - raw).max())
            assert max_delta < 1e-4
            for gid, values in line_ids.items():
                v = vertices(svg.find(f'.//*[@id="{gid}"]'))
                assert v.shape == (expected_days, 2)
                np.testing.assert_allclose(
                    v[:, 0],
                    xscale * np.arange(expected_days) + xshift,
                    atol=2e-6,
                    rtol=0,
                )
                delta = float(abs((v[:, 1] - yshift) / yscale - values).max())
                assert delta < 1e-4, (gid, delta)
                max_delta = max(max_delta, delta)
                checked += len(values)
            axis = svg.find(f'.//*[@id="axes_{p + 1}"]')
            # Tick labels confirm the physical displacement units of the affine map.
            for tick in axis.findall(".//s:g", NS):
                if tick.get("id", "").startswith("ytick_"):
                    t = tick.find(".//s:text", NS)
                    u = tick.find(".//s:use", NS)
                    value = float("".join(t.itertext()).replace("−", "-"))
                    assert abs((float(u.get("y")) - yshift) / yscale - value) < 1e-4
            if name in cfg["arms"]:
                for level in (80, 95):
                    band = vertices(
                        svg.find(f'.//*[@id="band_{name}_{point}_{level}"]')
                    )
                    days = np.rint((band[:, 0] - xshift) / xscale).astype(int)
                    assert set(days) == set(range(1168, 1461))
                    mm = (band[:, 1] - yshift) / yscale
                    z = ndtri((1 + level / 100) / 2)
                    for day in range(1168, 1461):
                        mu = source[name][day, p] - truth[0, p]
                        sd = source[name + "__sigma"][p]
                        delta = max(
                            abs(mm[days == day].min() - (mu - z * sd)),
                            abs(mm[days == day].max() - (mu + z * sd)),
                        )
                        assert delta < 1e-4
                        max_delta = max(max_delta, float(delta))
                        checked += 2
                a = metrics.loc[(name, point), "rmse"]
                b = metrics.loc[("BPLUS_CONTINUOUS", point), "rmse"]
                assert f"模型 {a:.2f} mm" in text and f"B+ {b:.2f} mm" in text
                np.testing.assert_allclose(
                    numbers.loc[(name, point), ["model_rmse", "bplus_rmse"]].to_numpy(
                        float
                    ),
                    [a, b],
                    atol=1e-10,
                    rtol=0,
                )
            max_svg_difference_mm = max(max_svg_difference_mm, max_delta)
            panel_rows.append(
                dict(
                    figure=name,
                    point=point,
                    all_dates_preserved=True,
                    curve_values_checked=True,
                    axis_units_verified=True,
                    interval="80/95% frozen marginal"
                    if name in cfg["arms"]
                    else "mean only; intervals in companion figures",
                    alignment="PASS",
                    text_geometry="PASS",
                )
            )
        exports.append(
            dict(
                figure=name,
                width_mm=width,
                height_mm=height,
                png_pixels=dimensions,
                font="Arial Unicode MS",
                missing_glyphs=missing,
            )
        )
    pd.DataFrame(panel_rows).to_csv(out / "panel_qa.csv", index=False)
    receipt = dict(
        status="passed",
        time_utc=utc(),
        figures=5,
        panels=20,
        curve_and_band_values_checked=checked,
        max_svg_quantization_difference_mm=max_svg_difference_mm,
        svg_tolerance_mm=1e-4,
        reason="six-decimal SVG coordinate serialization only; raw model tolerances unchanged",
        exports=exports,
        excluded_dates=0,
        pdf_audits="not run: user explicitly requested no PDF",
        static_preflight="rendered SVG geometry, fonts, text collisions and data checked directly; no PDF by user request",
        visual_inspection="pending separate human-visible image inspection; not inferred from automated checks",
    )
    write_json(out / "delivery_qa.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
