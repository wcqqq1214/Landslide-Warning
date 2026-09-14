"""Inspect delivered SVG geometry against immutable model/interval arrays."""

import json
import re
import xml.etree.ElementTree as ET

from matplotlib import font_manager, ft2font
import numpy as np
import pandas as pd
from PIL import Image
from scipy.special import ndtri

from transformer_regularization.qa_figures import NS, affine, vertices
from .core import B, ROOT, load_npz, read_json, read_labels, sha, spec, utc, write_json


def main():
    cfg = spec()
    root = ROOT / cfg["out"]
    out = ROOT / cfg["figures"] / "v1"
    source = load_npz(out / "source_arrays.npz")
    issue = load_npz(root / "final_exploratory/issued_distribution.npz")
    full = load_npz(root / "final_exploratory/full_means.npz")
    truth = read_labels(ROOT / cfg["data"], 1461)
    np.testing.assert_array_equal(source["observed"], truth)
    np.testing.assert_array_equal(source["y0"], truth[0])
    np.testing.assert_array_equal(source["full_dates"], full["dates"])
    for k, a in issue.items():
        np.testing.assert_array_equal(source[k], a)
    for k, a in full.items():
        if k != "dates":
            np.testing.assert_array_equal(source[k], a)
    metrics = pd.read_csv(root / "final_exploratory/metrics_by_point.csv").set_index(
        ["model", "rule", "point"]
    )
    numbers = pd.read_csv(out / "figure_numbers.csv").set_index(["figure", "point"])
    manifest = read_json(out / "manifest.json")
    for name, h in manifest["files"].items():
        assert sha(out / name) == h, name
    assert manifest["source_script_sha256"] == sha(
        ROOT / "code/transformer_calibration/figures.py"
    )
    font = font_manager.findfont("Arial Unicode MS", fallback_to_default=False)
    chars = ft2font.FT2Font(font).get_charmap()
    checked = 0
    max_delta = 0.0
    panel_rows = []
    exports = []
    for export in manifest["exports"]:
        name = export["figure"]
        svg = ET.parse(out / (name + ".svg")).getroot()
        text = "\n".join("".join(t.itertext()) for t in svg.findall(".//s:text", NS))
        width = float(svg.get("width").replace("pt", "")) * 25.4 / 72
        height = float(svg.get("height").replace("pt", "")) * 25.4 / 72
        assert abs(width - 240) < 1e-5 and abs(height - 170) < 1e-5
        for word in ("ATU1", "ATU5", "MJ3", "MJ1", "293", "实测", "库水位"):
            assert word in text, (name, word)
        missing = sorted({c for c in text if not c.isspace() and ord(c) not in chars})
        assert not missing, missing
        font_sizes = []
        for t in svg.findall(".//s:text", NS):
            match = re.search(r"font-size:\s*([\d.]+)px", t.get("style", ""))
            assert match, (name, t.attrib)
            font_sizes.append(float(match[1]))
        assert min(font_sizes) >= 7.6
        # Filled interval polygons also use SVG defs; exclude only the named bands
        # whose complete vertices are checked against the numerical arrays below.
        interval_paths = {
            path.get("id")
            for group in svg.findall(".//s:g", NS)
            if group.get("id", "").startswith("band_")
            for path in group.findall(".//s:defs/s:path", NS)
        }
        for path in svg.findall(".//s:defs/s:path", NS):
            if (
                path.get("id", "").startswith("m")
                and path.get("id") not in interval_paths
            ):
                assert len(re.findall(r"[ML]", path.get("d", ""))) <= 2, (
                    "Unexpected decorative marker"
                )
        assert read_json(out / (name + ".alignment.json"))["verdict"] == "PASS"
        assert read_json(out / (name + ".text_geometry.json"))["passed"]
        with Image.open(out / (name + ".png")) as im:
            assert (
                abs(im.width - 240 / 25.4 * 300) <= 1
                and abs(im.height - 170 / 25.4 * 300) <= 1
            )
            assert all(abs(v - 300) < 0.01 for v in im.info["dpi"])
            pixels = list(im.size)
        comparison = export["model"] == "all"
        for p, point in enumerate(cfg["points"]):
            if comparison:
                observed = truth[1168:, p] - truth[0, p]
                ref = vertices(svg.find(f'.//*[@id="comparison_observed_{point}"]'))
                n = 293
                curves = {
                    f"comparison_{m}_{point}": issue[m + "__mean"][:, p] - truth[0, p]
                    for m in cfg["methods"]
                }
            else:
                m, r = export["model"], export["rule"]
                observed = truth[:, p] - truth[0, p]
                ref = vertices(svg.find(f'.//*[@id="observed_{point}"]'))
                n = 1461
                curves = {
                    f"bplus_{point}": full[B][:, p] - truth[0, p],
                    f"model_{point}": full[m][:, p] - truth[0, p],
                }
            assert ref.shape == (n, 2)
            xs, xb = affine(np.arange(n), ref[:, 0])
            ys, yb = affine(observed, ref[:, 1])
            assert xs > 0 and ys < 0
            delta = float(abs((ref[:, 1] - yb) / ys - observed).max())
            assert delta < 1e-4
            max_delta = max(max_delta, delta)
            checked += n
            for gid, raw in curves.items():
                group = svg.find(f'.//*[@id="{gid}"]')
                v = vertices(group)
                assert v.shape == (n, 2), (gid, v.shape)
                np.testing.assert_allclose(
                    v[:, 0], xs * np.arange(n) + xb, atol=2e-6, rtol=0
                )
                delta = float(abs((v[:, 1] - yb) / ys - raw).max())
                assert delta < 1e-4, (name, gid, delta)
                max_delta = max(max_delta, delta)
                checked += n
                if not comparison:
                    line = group.find(".//s:path", NS)
                    clip_id = re.search(
                        r"url\(#([^)]+)\)", line.get("clip-path")
                    ).group(1)
                    rect = svg.find(f'.//*[@id="{clip_id}"]/s:rect', NS)
                    ylim = [
                        (float(rect.get("y")) + float(rect.get("height")) - yb) / ys,
                        (float(rect.get("y")) - yb) / ys,
                    ]
                    np.testing.assert_allclose(
                        ylim,
                        numbers.loc[(name, point), ["ylim_low", "ylim_high"]].to_numpy(
                            float
                        ),
                        atol=1e-4,
                        rtol=0,
                    )
            axis = svg.find(f'.//*[@id="axes_{p + 1}"]')
            ticks = {"x": [], "y": []}
            for kind in ("x", "y"):
                for group in axis.findall(".//s:g", NS):
                    if group.get("id", "").startswith(kind + "tick_"):
                        paths = group.findall(".//s:path", NS)
                        assert any(
                            "#d9dde1" in path.get("style", "") for path in paths
                        ), (name, point, kind)
                        if kind == "y":
                            t = group.find(".//s:text", NS)
                            u = group.find(".//s:use", NS)
                            val = float("".join(t.itertext()).replace("−", "-"))
                            ticks[kind].append(val)
                            assert abs((float(u.get("y")) - yb) / ys - val) < 1e-4
                assert (
                    axis.find(
                        f'.//*[@id="matplotlib.axis_{2 * p + (1 if kind == "x" else 2)}"]'
                    )
                    is not None
                )
            if not comparison:
                m, r = export["model"], export["rule"]
                if export["display"] == "mentor":
                    assert ticks["y"] == cfg["figure_contract"]["mentor_ticks"][p]
                for level in (80, 95):
                    band = vertices(svg.find(f'.//*[@id="band_{point}_{level}"]'))
                    days = np.rint((band[:, 0] - xb) / xs).astype(int)
                    assert set(days) == set(range(1168, 1461))
                    mm = (band[:, 1] - yb) / ys
                    z = ndtri((1 + level / 100) / 2)
                    mu = full[m][1168:, p] - truth[0, p]
                    sd = issue[m + "__" + r + "__sigma"][:, p]
                    for j, day in enumerate(range(1168, 1461)):
                        delta = max(
                            abs(mm[days == day].min() - (mu[j] - z * sd[j])),
                            abs(mm[days == day].max() - (mu[j] + z * sd[j])),
                        )
                        assert delta < 1e-4, (name, point, level, day, delta)
                        max_delta = max(max_delta, float(delta))
                        checked += 2
                    limits = numbers.loc[
                        (name, point), ["ylim_low", "ylim_high"]
                    ].to_numpy(float)
                    clipped = int(
                        ((mu - z * sd < limits[0]) | (mu + z * sd > limits[1])).sum()
                    )
                    assert (
                        numbers.loc[(name, point), f"band{level}_days_outside_axes"]
                        == clipped
                    )
                assert f"模型 {metrics.loc[(m, r, point), 'rmse']:.2f} mm" in text
                assert f"B+ {metrics.loc[(B, r, point), 'rmse']:.2f} mm" in text
                np.testing.assert_allclose(
                    numbers.loc[(name, point), ["model_rmse", "bplus_rmse"]].to_numpy(
                        float
                    ),
                    [
                        metrics.loc[(m, r, point), "rmse"],
                        metrics.loc[(B, r, point), "rmse"],
                    ],
                    atol=1e-9,
                    rtol=0,
                )
                if export["clipped_display"]:
                    assert "部分区间超出参考图框" in text
            panel_rows.append(
                dict(
                    figure=name,
                    point=point,
                    all_dates_preserved=True,
                    curves_verified=True,
                    interval_values_verified=not comparison,
                    xy_grid=True,
                    no_triangle_markers=True,
                    alignment="PASS",
                    text_geometry="PASS",
                )
            )
        exports.append(
            dict(
                figure=name,
                pixels=pixels,
                width_mm=width,
                height_mm=height,
                min_font_pt=min(font_sizes),
                missing_glyphs=missing,
            )
        )
    pd.DataFrame(panel_rows).to_csv(out / "panel_qa.csv", index=False)
    result = dict(
        status="passed",
        time_utc=utc(),
        figures=len(exports),
        panels=len(panel_rows),
        curve_and_band_values_checked=checked,
        max_svg_quantization_difference_mm=max_delta,
        svg_tolerance_mm=1e-4,
        raw_numerical_tolerances_unchanged=True,
        source_arrays_match=True,
        exports=exports,
        pdf_audits="not run: explicit user no-PDF request",
        visual_inspection="pending",
    )
    write_json(out / "delivery_qa.json", result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
