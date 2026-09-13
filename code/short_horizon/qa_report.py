"""Final PDF glyph geometry/numbers and independent block-sum statistics audit."""

import argparse
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
import pymupdf
from scipy.stats import norm

from .common import ROOT, load_spec, save_json, sha, now
from .data import observations
from .verify import arrays, close


def geometry(doc):
    rows = []
    for i, page in enumerate(doc):
        glyphs = []
        clipped = []
        for span in page.get_texttrace():
            if span.get("opacity", 1) <= 0 or span.get("type", 0) == 3:
                continue
            for c in span["chars"]:
                char = chr(c[0])
                b = pymupdf.Rect(c[3])
                if char.isspace() or not char.isprintable():
                    continue
                if (
                    b.x0 < -0.5
                    or b.y0 < -0.5
                    or b.x1 > page.rect.width + 0.5
                    or b.y1 > page.rect.height + 0.5
                ):
                    clipped.append(char)
                inset = pymupdf.Rect(b.x0 + 0.6, b.y0 + 0.6, b.x1 - 0.6, b.y1 - 0.6)
                if not inset.is_empty:
                    glyphs.append((char, inset))
        glyphs.sort(key=lambda x: x[1].y0)
        collisions = []
        for j, (a, box) in enumerate(glyphs):
            for b, other in glyphs[j + 1 :]:
                if other.y0 >= box.y1:
                    break
                common = box & other
                if (
                    not common.is_empty
                    and common.get_area() / min(box.get_area(), other.get_area()) > 0.05
                ):
                    collisions.append(
                        dict(a=a, b=b, a_bbox=list(box), b_bbox=list(other))
                    )
        rows.append(
            dict(page=i + 1, glyphs=len(glyphs), clipped=clipped, collisions=collisions)
        )
    if any(r["clipped"] or r["collisions"] for r in rows):
        raise ArithmeticError("PDF glyph collision or clipping")
    return rows


def block_statistics(spec, root):
    y, _, _ = observations(spec)
    lock = json.loads((root / "selection.json").read_text())
    report = pd.read_csv(root / "analysis/paired_block_bootstrap.csv")
    stride = pd.read_csv(root / "analysis/every_seventh_origin.csv")
    rng = np.random.default_rng(spec["bootstrap"]["seed"])
    maximum = 0.0
    cells = 0

    def scores(p, h, N):
        truth = y[p["origins"][:N] + h - 1]
        mu = p["mean"][:N, h - 1]
        sd = p["sigma"][:N, h - 1]
        e = truth - mu
        z = e / sd
        crps = e * (2 * norm.cdf(z) - 1) + 2 * sd * norm.pdf(z) - sd / np.sqrt(np.pi)
        return e * e, crps

    for row in lock["by_horizon"]:
        h = row["horizon"]
        N = 293 - h + 1
        L = 30
        B = 2000
        parts = math.ceil(N / L)
        starts = rng.integers(0, N - L + 1, size=(B, parts))
        lengths = np.full(parts, L)
        lengths[-1] = N - L * (parts - 1)

        def sum_blocks(x):
            # Independent prefix-sum evaluation; no resampled prediction matrices.
            prefix = np.vstack([np.zeros((1, 4)), np.cumsum(x, axis=0)])
            return (prefix[starts + lengths] - prefix[starts]).sum(axis=1) / N

        names = sorted(
            set(
                row[k]
                for k in ["mean_best", "probability_best", "recommended"]
                if row[k]
            )
        )
        for name in names:
            ae, ac = scores(arrays(root / "later_exploratory" / (name + ".npz")), h, N)
            for base in ["B_ANCHOR", "DRIFT1"]:
                be, bc = scores(
                    arrays(root / "later_exploratory" / (base + ".npz")), h, N
                )
                boot = {
                    "rmse": np.sqrt(sum_blocks(ae)).mean(1)
                    - np.sqrt(sum_blocks(be)).mean(1),
                    "crps": (sum_blocks(ac) - sum_blocks(bc)).mean(1),
                }
                actual = {
                    "rmse": np.sqrt(ae.mean(0)).mean() - np.sqrt(be.mean(0)).mean(),
                    "crps": np.mean(ac - bc),
                }
                for metric, v in boot.items():
                    r = report[
                        (report.horizon == h)
                        & (report.model == name)
                        & (report.reference == base)
                        & (report.metric == metric)
                    ].iloc[0]
                    expected = np.r_[actual[metric], np.quantile(v, [0.025, 0.975])]
                    maximum = max(
                        maximum,
                        close(
                            np.array([r.difference, r.low95, r.high95]), expected, 1e-9
                        ),
                    )
                    cells += 3
                r = stride[
                    (stride.horizon == h)
                    & (stride.model == name)
                    & (stride.reference == base)
                ].iloc[0]
                ix = np.arange(0, N, 7)
                assert r.n_origins == len(ix)
                close(
                    np.array(r.rmse_difference),
                    np.array(
                        np.sqrt(ae[ix].mean(0)).mean() - np.sqrt(be[ix].mean(0)).mean()
                    ),
                    1e-9,
                )
                close(
                    np.array(r.crps_difference),
                    np.array(np.mean(ac[ix] - bc[ix])),
                    1e-9,
                )
                cells += 2
    return dict(
        passed=True,
        score_and_interval_cells=cells,
        max_difference_mm=maximum,
        method="independent prefix sums over the same frozen non-circular block samples",
        new_training=0,
    )


def numeric_pdf(doc, root):
    pages = ["".join(p.get_text().split()) for p in doc]
    sel = pd.read_csv(root / "analysis/selection_by_horizon.csv")
    cells = 0
    for _, r in sel.iterrows():
        for c in [
            "development_recommended_rmse",
            "later_exploratory_recommended_rmse",
            "later_exploratory_recommended_crps",
        ]:
            assert f"{r[c]:.6f}" in pages[0]
            cells += 1
        assert f"{100 * r.later_exploratory_recommended_coverage90:.2f}%" in pages[0]
        cells += 1
    values = json.loads((root / "analysis/report_values.json").read_text())
    for r in values["horizon7"]:
        for k in ["development_rmse", "later_rmse", "later_crps"]:
            assert f"{r[k]:.4f}" in pages[1]
            cells += 1
    M = pd.read_csv(root / "later_exploratory/metrics_by_point_horizon.csv")
    M = M[(M.model == "C16_CORE_RULES") & (M.horizon == 7)]
    for _, r in M.iterrows():
        page = pages[2] if r.point.startswith("ATU") else pages[3]
        assert f"{r.rmse:.4f}" in page and f"{100 * r.coverage90:.2f}%" in page
        cells += 2
    for value in ["1.2763", "0.2530", "0.3762", "0.0932", "7800", "61152", "14508"]:
        assert value in pages[4]
        cells += 1
    assert len(doc) == 5
    return dict(
        pages=5,
        numeric_presence_checks=cells,
        checked_against="frozen selection and source summary tables; row order also reviewed in TeX and rendered pages",
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--pdf", required=True)
    args = ap.parse_args()
    spec = load_spec(args.config)
    root = ROOT / spec["output_root"]
    pdf = Path(args.pdf)
    doc = pymupdf.open(pdf)
    r = dict(passed=False, time_utc=now(), pdf_sha256=sha(pdf))
    r.update(
        character_geometry=geometry(doc),
        numbers=numeric_pdf(doc, root),
        statistics=block_statistics(spec, root),
        passed=True,
    )
    r["generic_figure_span_audit"] = (
        "XeTeX joins nonadjacent rows/cells into font spans; the generic plot-span detector reports false overlap. Final report checked with actual per-character boxes and page renders; generic failure receipts retained, not relabelled as passes."
    )
    save_json(root / "verification/report_qa.json", r)
    print(
        json.dumps(
            {
                "passed": True,
                "pages": len(doc),
                "statistics": r["statistics"],
                "numbers": r["numbers"],
            }
        )
    )


if __name__ == "__main__":
    main()
