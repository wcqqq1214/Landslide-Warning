"""Check actual SVG data, source metrics, export geometry and PDF structure."""

import argparse
import re
import xml.etree.ElementTree as ET

from matplotlib import font_manager, ft2font
import numpy as np
import pandas as pd
from PIL import Image
import pymupdf
from scipy.stats import norm

from transformer_regularization.qa_figures import NS, NUMBER, affine, vertices
from transformer_temporal.audit import independent_scores
from .core import (
    B,
    ROOT,
    bank,
    load_npz,
    read_forcing,
    read_json,
    read_labels,
    sha,
    spec,
    utc,
    write_json,
)


def main(attempt):
    cfg = spec()
    root, out = ROOT / cfg["out"], ROOT / cfg["figures"] / attempt
    source, manifest = (
        load_npz(out / "source_arrays.npz"),
        read_json(out / "manifest.json"),
    )
    truth = read_labels(ROOT / cfg["data"], 1461)
    _, dates = read_forcing(ROOT / cfg["data"], 1461)
    np.testing.assert_array_equal(source["observed"], truth)
    np.testing.assert_array_equal(source["dates"], dates)
    np.testing.assert_array_equal(source["y0"], truth[0])
    np.testing.assert_array_equal(source["bplus_full"], bank(cfg)[1168]["mean"])
    for file, suffix in [("means", ""), ("seeds", "__seeds"), ("sigmas", "__sigma")]:
        for k, v in load_npz(root / f"base/origin_1168/{file}.npz").items():
            np.testing.assert_array_equal(source[k + suffix], v)
    for name, h in manifest["files"].items():
        assert sha(out / name) == h
    assert manifest["source_script_sha256"] == sha(
        ROOT / "code/overnight_graph/figures.py"
    )
    assert manifest["contract_sha256"] == sha(
        ROOT / "docs/ootang_overnight_graph_figure_contract.v1.0.md"
    )
    pdfpath = ROOT / manifest["pdf"]
    assert sha(pdfpath) == manifest["pdf_sha256"]
    point = pd.read_csv(root / "analysis/metrics_by_point.csv").set_index(
        ["origin", "method", "point"]
    )
    summary = pd.read_csv(root / "analysis/phase_summary.csv").set_index(
        ["origin", "method"]
    )
    seeds = pd.read_csv(root / "analysis/seed_summary.csv").set_index(
        ["origin", "method", "seed"]
    )
    var = pd.read_csv(root / "diagnostic/variances.csv").set_index(["start", "point"])
    decision = read_json(root / "diagnostic/decision.json")
    nums = pd.read_csv(out / "figure_numbers.csv").set_index(["figure", "point"])
    fontmap = ft2font.FT2Font(
        font_manager.findfont("Arial Unicode MS", fallback_to_default=False)
    ).get_charmap()
    checked, maximum, panels, exports = 0, 0.0, [], []
    with pymupdf.open(pdfpath) as doc:
        assert len(doc) == 6
        for page in doc:
            assert abs(page.rect.width * 25.4 / 72 - 240) < 1e-4
            assert abs(page.rect.height * 25.4 / 72 - 170) < 1e-4
            assert page.get_text().strip() and "\ufffd" not in page.get_text()
            assert not page.get_images(), (
                "Scientific PDF should contain vector geometry and embedded font text"
            )
            for font in page.get_fonts(full=True):
                assert len(doc.extract_font(font[0])[3]) > 0, "Font is not embedded"
    for exp in manifest["exports"]:
        name = exp["figure"]
        svg = ET.parse(out / f"{name}.svg").getroot()
        text = "\n".join("".join(t.itertext()) for t in svg.findall(".//s:text", NS))
        sizes = []
        for t in svg.findall(".//s:text", NS):
            m = re.search(r"font-size:\s*([\d.]+)px", t.get("style", ""))
            if m:
                sizes.append(float(m[1]))
        assert sizes and min(sizes) >= 5
        assert not {c for c in text if not c.isspace() and ord(c) not in fontmap}
        assert abs(float(svg.get("width").replace("pt", "")) * 25.4 / 72 - 240) < 1e-5
        assert abs(float(svg.get("height").replace("pt", "")) * 25.4 / 72 - 170) < 1e-5
        assert read_json(out / f"{name}.alignment.json")["verdict"] == "PASS"
        assert read_json(out / f"{name}.text_geometry.json")["passed"]
        with Image.open(out / f"{name}.png") as im:
            assert (
                abs(im.width - 240 / 25.4 * 300) <= 1
                and abs(im.height - 170 / 25.4 * 300) <= 1
            )
            assert all(abs(d - 300) < 0.01 for d in im.info["dpi"])
        if exp["kind"] == "trajectory":
            method = exp["method"]
            for p, pname in enumerate(cfg["points"]):
                observed = truth[:, p] - truth[0, p]
                v = vertices(svg.find(f'.//*[@id="observed_{pname}"]'))
                assert v.shape == (1461, 2)
                xs, xb = affine(np.arange(1461), v[:, 0])
                ys, yb = affine(observed, v[:, 1])
                assert xs > 0 and ys < 0
                for gid, raw, ix in [
                    ("observed", observed, np.arange(1461)),
                    (
                        "bplus",
                        source["bplus_full"][:, p] - truth[0, p],
                        np.arange(1461),
                    ),
                    (
                        "model",
                        source[method][:, p] - truth[0, p],
                        np.arange(1168, 1461),
                    ),
                ]:
                    v = vertices(svg.find(f'.//*[@id="{gid}_{pname}"]'))
                    assert v.shape == (len(ix), 2)
                    np.testing.assert_allclose(v[:, 0], xs * ix + xb, atol=2e-6, rtol=0)
                    delta = float(np.max(abs((v[:, 1] - yb) / ys - raw)))
                    assert delta < 1e-4, (name, pname, gid, delta)
                    checked += len(raw)
                    maximum = max(maximum, delta)
                mu = source[method][:, p] - truth[0, p]
                limits = nums.loc[(name, pname), ["ylim_low", "ylim_high"]].to_numpy(
                    float
                )
                for level in [80, 95]:
                    band = vertices(svg.find(f'.//*[@id="band_{pname}_{level}"]'))
                    days = np.rint((band[:, 0] - xb) / xs).astype(int)
                    assert set(days) == set(range(1168, 1461))
                    raw = (band[:, 1] - yb) / ys
                    q = norm.ppf((1 + level / 100) / 2) * source[method + "__sigma"][p]
                    lo, hi = mu - q, mu + q
                    for j, d in enumerate(range(1168, 1461)):
                        delta = max(
                            abs(raw[days == d].min() - lo[j]),
                            abs(raw[days == d].max() - hi[j]),
                        )
                        assert delta < 1e-4
                        maximum = max(maximum, float(delta))
                        checked += 2
                    assert (
                        int(((lo < limits[0]) | (hi > limits[1])).sum())
                        == nums.loc[(name, pname), f"band{level}_outside_days"]
                    )
                vals = np.r_[mu, observed, source["bplus_full"][:, p] - truth[0, p]]
                outside = int(((vals < limits[0]) | (vals > limits[1])).sum())
                assert (
                    outside
                    == nums.loc[(name, pname), "mean_observed_outside_values"]
                    == 0
                )
                if exp["display"] == "full":
                    assert (
                        nums.loc[
                            (name, pname),
                            ["band80_outside_days", "band95_outside_days"],
                        ]
                        == 0
                    ).all()
                for key, m in [("rmse", method), ("bplus_rmse", B)]:
                    assert (
                        abs(
                            nums.loc[(name, pname), key]
                            - point.loc[(1168, m, pname), "rmse"]
                        )
                        < 1e-9
                    )
                assert f"模型 {point.loc[(1168, method, pname), 'rmse']:.2f} mm" in text
                assert f"B+ {point.loc[(1168, B, pname), 'rmse']:.2f} mm" in text
                panels.append(
                    dict(
                        figure=name,
                        panel=pname,
                        role="complete point-specific trajectory",
                        center="three-seed ensemble, 293 forecast days; full observed/B+ history",
                        uncertainty="80/95% Gaussian marginal prediction interval; not seed CI",
                        replicate="three optimizer seeds; one landslide",
                        numeric="passed",
                        alignment="PASS",
                        visual="pending",
                    )
                )
        else:
            for rec in [
                r for r in read_json(out / "plot_records.json") if r["figure"] == name
            ]:
                g = svg.find(f'.//*[@id="{rec["gid"]}"]')
                if rec["kind"] == "markers":
                    actual = np.array(
                        [
                            [float(u.get("x")), float(u.get("y"))]
                            for u in g.findall(".//s:use", NS)
                        ]
                    )
                else:
                    actual = np.array(
                        re.findall(
                            r"[ML]\s*(" + NUMBER + r")\s+(" + NUMBER + r")",
                            g.find(".//s:path", NS).get("d"),
                        ),
                        float,
                    )
                np.testing.assert_allclose(actual, rec["svg_xy"], atol=2e-6, rtol=0)
                m, s, recipe = rec["method"], rec["seed"], rec["recipe"]
                tab = summary if s is None else seeds

                def metric(n, model, key):
                    return tab.loc[(n, model) if s is None else (n, model, s), key]

                if recipe == "ratio":
                    expected = [
                        metric(n, m, "rmse") / summary.loc[(n, B), "rmse"]
                        for n in cfg["origins"][1:]
                    ]
                elif recipe == "paired":
                    expected = [
                        metric(n, "GRU_GRAPH", "rmse") - metric(n, "GRU_LOCAL", "rmse")
                        for n in cfg["origins"][1:]
                    ]
                elif recipe == "fast_ratio":
                    expected = [
                        var.loc[(n, rec["point"]), "fast_ratio"]
                        for n in [432, 612, 792, 972]
                    ]
                elif s is None:
                    expected = [point.loc[(1168, m, p), recipe] for p in cfg["points"]]
                else:
                    expected = independent_scores(
                        truth[1168:], source[m + "__seeds"][s], source[m + "__sigma"]
                    )[recipe]
                np.testing.assert_allclose(rec["y"], expected, atol=1e-10, rtol=0)
                checked += len(expected)
            if exp["kind"] == "diagnostic":
                for pname in cfg["points"]:
                    n = max(
                        len(e["passing_blocks"])
                        for e in decision["eligible"]
                        if e["point"] == pname
                    )
                    assert f"最多 {n}/4 块" in text
                assert (
                    decision["triggered"] is False and not decision["eligible_points"]
                )
                panel_labels = cfg["points"]
                roles = ["point-specific temporal replication of residual scale"] * 4
            else:
                panel_labels = "abcd"
                roles = [
                    "relative temporal error",
                    "matched spatial increment",
                    "point mean error",
                    "point probability score",
                ]
                g = summary.loc[(1168, "GRU_GRAPH")]
                assert f"覆盖{100 * g['coverage90']:.2f}%" in text
                assert f"宽度{g['width90']:.2f} mm" in text
            for label, role in zip(panel_labels, roles):
                panels.append(
                    dict(
                        figure=name,
                        panel=label,
                        role=role,
                        center="ensemble score or fixed residual variance ratio",
                        uncertainty="all three seed scores in comparison; descriptive ratio has no CI",
                        replicate="overlapping time windows, four points, one landslide",
                        numeric="passed",
                        alignment="PASS",
                        visual="pending",
                    )
                )
        exports.append(dict(figure=name, page=exp["page"], min_font_pt=min(sizes)))
    assert len(panels) == 24
    pd.DataFrame(panels).to_csv(out / "panel_audit.csv", index=False)
    write_json(
        out / "delivery_qa.json",
        dict(
            status="numerical_passed_visual_pending",
            time_utc=utc(),
            figures=len(exports),
            panels=len(panels),
            values_checked=checked,
            max_svg_quantization_difference_mm=maximum,
            exports=exports,
            audit_script_sha256=sha(ROOT / "code/overnight_graph/qa_figures.py"),
            pdf_pages=6,
            pdf_fonts="embedded",
            pdf_vector=True,
            visual_inspection="pending",
        ),
    )
    print(read_json(out / "delivery_qa.json"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", default="v1")
    main(parser.parse_args().attempt)
