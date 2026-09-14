"""Verify actual exported SVG vertices/markers against saved prediction arrays."""

import re
import xml.etree.ElementTree as ET

from matplotlib import font_manager, ft2font
import numpy as np
import pandas as pd
from PIL import Image
from scipy.stats import norm

from transformer_regularization.qa_figures import NS, NUMBER, affine, vertices
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
    np.testing.assert_array_equal(source["bplus_full"], bank(cfg)[1168]["mean"])
    for file, suffix in [("means", ""), ("seeds", "__seeds"), ("sigmas", "__sigma")]:
        for k, v in load_npz(root / f"origin_1168/{file}.npz").items():
            np.testing.assert_array_equal(source[k + suffix], v)
    for name, h in manifest["files"].items():
        assert sha(out / name) == h
    assert manifest["source_script_sha256"] == sha(
        ROOT / "code/transformer_origin/figures.py"
    )
    assert manifest["contract_sha256"] == sha(
        ROOT / "docs/ootang_transformer_origin_figure_contract.v1.0.md"
    )
    points = pd.read_csv(root / "analysis/metrics_by_point.csv").set_index(
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
    checked, maximum = 0, 0.0
    exports = []
    panel_rows = []
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
        assert not {c for c in text if not c.isspace() and ord(c) not in charset}
        assert abs(float(svg.get("width").replace("pt", "")) * 25.4 / 72 - 240) < 1e-5
        assert abs(float(svg.get("height").replace("pt", "")) * 25.4 / 72 - 170) < 1e-5
        assert read_json(out / (name + ".alignment.json"))["verdict"] == "PASS"
        assert read_json(out / (name + ".text_geometry.json"))["passed"]
        with Image.open(out / (name + ".png")) as im:
            assert (
                abs(im.width - 240 / 25.4 * 300) <= 1
                and abs(im.height - 170 / 25.4 * 300) <= 1
            )
            assert all(abs(d - 300) < 0.01 for d in im.info["dpi"])
        if export["kind"] == "trajectory":
            method = export["method"]
            for p, point in enumerate(cfg["points"]):
                observed = truth[:, p] - truth[0, p]
                v = vertices(svg.find(f'.//*[@id="observed_{point}"]'))
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
                    v = vertices(svg.find(f'.//*[@id="{gid}_{point}"]'))
                    assert v.shape == (len(ix), 2)
                    np.testing.assert_allclose(v[:, 0], xs * ix + xb, atol=2e-6, rtol=0)
                    delta = float(np.max(abs((v[:, 1] - yb) / ys - raw)))
                    assert delta < 1e-4, (name, point, gid, delta)
                    maximum = max(maximum, delta)
                    checked += len(raw)
                mu = source[method][:, p] - truth[0, p]
                limits = nums.loc[(name, point), ["ylim_low", "ylim_high"]].to_numpy(
                    float
                )
                for level in [80, 95]:
                    band = vertices(svg.find(f'.//*[@id="band_{point}_{level}"]'))
                    day = np.rint((band[:, 0] - xb) / xs).astype(int)
                    assert set(day) == set(range(1168, 1461))
                    raw = (band[:, 1] - yb) / ys
                    q = norm.ppf((1 + level / 100) / 2) * source[method + "__sigma"][p]
                    lo, hi = mu - q, mu + q
                    for j, d in enumerate(range(1168, 1461)):
                        delta = max(
                            abs(raw[day == d].min() - lo[j]),
                            abs(raw[day == d].max() - hi[j]),
                        )
                        assert delta < 1e-4
                        maximum = max(maximum, float(delta))
                        checked += 2
                    assert (
                        int(((lo < limits[0]) | (hi > limits[1])).sum())
                        == nums.loc[(name, point), f"band{level}_outside_days"]
                    )
                values = np.r_[mu, observed, source["bplus_full"][:, p] - truth[0, p]]
                assert (
                    int(((values < limits[0]) | (values > limits[1])).sum())
                    == nums.loc[(name, point), "mean_observed_outside_values"]
                )
                for key, m in [("rmse", method), ("bplus_rmse", B)]:
                    assert (
                        abs(
                            nums.loc[(name, point), key]
                            - points.loc[(1168, m, point), "rmse"]
                        )
                        < 1e-9
                    )
                assert (
                    f"模型 {points.loc[(1168, method, point), 'rmse']:.2f} mm" in text
                )
                assert f"B+ {points.loc[(1168, B, point), 'rmse']:.2f} mm" in text
                axis = svg.find(f'.//*[@id="axes_{p + 1}"]')
                for kind in ["x", "y"]:
                    ticks = [
                        g
                        for g in axis.findall(".//s:g", NS)
                        if g.get("id", "").startswith(kind + "tick_")
                    ]
                    assert ticks
                    assert all(
                        any(
                            "#d9dde1" in path.get("style", "")
                            for path in g.findall(".//s:path", NS)
                        )
                        for g in ticks
                    )
                panel_rows.append(
                    dict(
                        figure=name,
                        panel=point,
                        role="complete point-specific trajectory",
                        center="ensemble prediction only after origin",
                        uncertainty="Gaussian 80/95% marginal prediction intervals",
                        replicate="three fixed seeds",
                        numeric="passed",
                        alignment="PASS",
                        geometry="PASS",
                        visual="pending",
                    )
                )
        else:
            ns = cfg["origins"][1:]
            for r in read_json(out / "plot_records.json"):
                group = svg.find(f'.//*[@id="{r["gid"]}"]')
                if r["kind"] == "markers":
                    actual = np.array(
                        [
                            [float(u.get("x")), float(u.get("y"))]
                            for u in group.findall(".//s:use", NS)
                        ]
                    )
                else:
                    actual = np.array(
                        re.findall(
                            r"[ML]\s*(" + NUMBER + r")\s+(" + NUMBER + r")",
                            group.find(".//s:path", NS).get("d"),
                        ),
                        float,
                    )
                np.testing.assert_allclose(actual, r["svg_xy"], atol=2e-6, rtol=0)
                m, s, recipe = r["method"], r["seed"], r["recipe"]
                table = summary if s is None else seeds

                def metric(n, method, k):
                    return table.loc[(n, method) if s is None else (n, method, s), k]

                if recipe == "ratio":
                    expected = [
                        metric(n, m, "rmse") / summary.loc[(n, B), "rmse"] for n in ns
                    ]
                elif recipe == "paired":
                    expected = [
                        metric(n, "COND_ATTN", "rmse") - metric(n, m, "rmse")
                        for n in ns
                    ]
                elif recipe == "coverage":
                    expected = [100 * metric(n, m, "coverage90") for n in ns]
                elif s is None:
                    expected = [points.loc[(1168, m, p), "rmse"] for p in cfg["points"]]
                else:
                    expected = np.sqrt(
                        np.mean((source[m + "__seeds"][s] - truth[1168:]) ** 2, axis=0)
                    )
                np.testing.assert_allclose(r["y"], expected, atol=1e-10, rtol=0)
                checked += len(expected)
            for label, role in zip(
                "abcd",
                [
                    "temporal error direction",
                    "paired information/attention comparison",
                    "final spatial error",
                    "temporal probability coverage",
                ],
            ):
                panel_rows.append(
                    dict(
                        figure=name,
                        panel=label,
                        role=role,
                        center="score of equal-seed ensemble forecast",
                        uncertainty="all three seed scores as faint points; no significance claim",
                        replicate="overlapping time windows and seeds",
                        numeric="passed",
                        alignment="PASS",
                        geometry="PASS",
                        visual="pending",
                    )
                )
        exports.append(
            dict(figure=name, min_font_pt=min(sizes), alignment="PASS", geometry="PASS")
        )
    pd.DataFrame(panel_rows).to_csv(out / "panel_audit.csv", index=False)
    write_json(
        out / "delivery_qa.json",
        dict(
            status="passed",
            time_utc=utc(),
            figures=len(exports),
            panels=12,
            values_checked=checked,
            max_svg_quantization_difference_mm=maximum,
            exports=exports,
            audit_script_sha256=sha(ROOT / "code/transformer_origin/qa_figures.py"),
            pdf_audits="not performed: user requested no PDF",
            visual_inspection="pending",
        ),
    )
    print(read_json(out / "delivery_qa.json"))


if __name__ == "__main__":
    main()
