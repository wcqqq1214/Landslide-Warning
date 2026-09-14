"""Read-only CSV/issued-endpoint/figure/report consistency audit."""

import argparse
import json
import re
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
from PIL import Image
from scipy.special import ndtri

from short_horizon.common import ROOT, load_spec, save_json, sha, now
from short_horizon.data import observations
from short_horizon.verify import close
from .run import arrays


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--output", default="verification/delivery_receipt.json")
    args = p.parse_args()
    spec = load_spec(args.config)
    root = ROOT / spec["output_root"]
    out = root / args.output
    if out.exists():
        raise FileExistsError(out)
    source = root / "analysis"
    figures = ROOT / spec["figures_root"] / "v3"
    y, _, dates = observations(spec)
    curves = pd.read_csv(source / "forecasts_long.csv")
    summary = pd.read_csv(source / "summary_by_horizon.csv")
    metrics = pd.read_csv(source / "metrics_by_point_horizon.csv")
    compare = pd.read_csv(source / "comparisons.csv")
    gates = pd.read_csv(source / "working_conditions.csv")
    assert len(curves) == 117600 and len(summary) == 105 and len(metrics) == 420
    maximum = 0.0
    groups = 0
    for (phase, name, h, point), frame in curves.groupby(
        ["phase", "model", "horizon", "point"], sort=False
    ):
        start, end = spec["stages"][phase]
        pidx = spec["points"].index(point)
        origins = np.arange(start, end - h + 1)
        assert np.array_equal(frame.origin, origins)
        assert np.array_equal(frame.target_date, dates[origins + h - 1])
        assert np.array_equal(frame.last_observed_date, dates[origins - 1])
        pred = arrays(root / phase / (name + ".npz"))
        mu, sd = (
            pred["mean"][: len(origins), h - 1, pidx],
            pred["sigma"][: len(origins), h - 1, pidx],
        )
        maximum = max(
            maximum,
            close(frame.mean_mm, mu, 1e-9),
            close(frame.sigma_mm, sd, 1e-9),
            close(frame.observed_mm, y[origins + h - 1, pidx], 1e-9),
        )
        for level in (80, 90, 95):
            q = ndtri((1 + level / 100) / 2)
            maximum = max(
                maximum,
                close(frame[f"lower{level}_mm"], mu - q * sd, 1e-9),
                close(frame[f"upper{level}_mm"], mu + q * sd, 1e-9),
            )
        groups += 1
    lookup = summary.set_index(["phase", "model", "horizon"])
    for _, row in compare.iterrows():
        a, b = (
            lookup.loc[(row.phase, row.candidate, row.horizon)],
            lookup.loc[(row.phase, row.reference, row.horizon)],
        )
        for key in ("mae", "rmse", "crps", "interval_score90", "coverage90", "width90"):
            close(np.array(row[key + "_difference"]), np.array(a[key] - b[key]), 1e-10)
            close(np.array(row[key + "_ratio"]), np.array(a[key] / b[key]), 1e-10)
        assert row.mean_win == bool(a.mae < b.mae - 1e-8 and a.rmse < b.rmse - 1e-8)
        assert row.probability_score_win == bool(
            a.crps < b.crps - 1e-8 and a.interval_score90 < b.interval_score90 - 1e-8
        )
    # Independently rebuild the numeric working conditions, including each point.
    point_lookup = metrics.set_index(["phase", "model", "horizon", "point"])
    gate_cells = 0
    for _, row in gates.iterrows():
        a = lookup.loc[(row.phase, row.model, row.horizon)]
        b = lookup.loc[(row.phase, "B_ANCHOR", row.horizon)]
        d = lookup.loc[(row.phase, "DRIFT1", row.horizon)]
        expected = {
            f"mean_{k}_better_B": a[k] < b[k] - 1e-8
            for k in ("mae", "rmse", "crps", "interval_score90")
        }
        expected.update(
            {f"no_worse_drift_{k}": a[k] <= d[k] + 1e-8 for k in ("rmse", "crps")}
        )
        expected["average_coverage90"] = 0.85 <= a.coverage90 <= 0.95
        expected["physical_contract"] = True
        for point in spec["points"]:
            a = point_lookup.loc[(row.phase, row.model, row.horizon, point)]
            b = point_lookup.loc[(row.phase, "B_ANCHOR", row.horizon, point)]
            expected.update(
                {point + "_" + k: a[k] <= b[k] + 1e-8 for k in ("mae", "rmse")}
            )
            expected.update(
                {
                    point + "_" + k: a[k] <= 1.05 * b[k] + 1e-8
                    for k in ("crps", "interval_score90")
                }
            )
            expected[point + "_coverage90"] = 0.8 <= a.coverage90 <= 0.98
        for k, value in expected.items():
            assert bool(row[k]) == bool(value), (row.phase, row.model, row.horizon, k)
            gate_cells += 1
        assert bool(row.passed) == all(expected.values())
    # The unscored boundary endpoints remain actual issued predictions, with causal scales.
    boundary_cells = 0
    boundary_difference = 0.0
    for phase, (start, end) in spec["stages"].items():
        previous = {
            "inner": None,
            "development": "inner",
            "later_exploratory": "development",
        }[phase]
        for calibration in (root / phase).rglob("*_calibration.json"):
            name = calibration.name.replace("_calibration.json", "")
            pred = arrays(calibration.with_name(name + ".npz"))
            issued = arrays(calibration.with_name(name + "_issued.npz"))
            prior = arrays(root / previous / (name + ".npz")) if previous else None
            for k, init in enumerate(
                json.loads(calibration.read_text())["initialization"]
            ):
                hist = [
                    y[o + k] - (y[o - 1] + (k + 1) * (y[o - 1] - y[o - 2]))
                    for o in init["drift_origins"]
                ]
                if init["model_origins"]:
                    assert prior is not None
                    hist.extend(
                        y[o + k]
                        - prior["mean"][int(np.where(prior["origins"] == o)[0][0]), k]
                        for o in init["model_origins"]
                    )
                assert len(hist) == 90
                for i, n in enumerate(pred["origins"]):
                    if n + k < end:
                        continue
                    mature = [
                        y[o + k] - issued["mean"][o - start, k]
                        for o in range(start, n - k)
                    ]
                    pool = np.asarray((hist + mature)[-90:])
                    expected = np.maximum(np.sqrt((pool * pool).mean(axis=0)), 1e-6)
                    boundary_difference = max(
                        boundary_difference,
                        close(issued["sigma"][i, k], expected, 1e-10),
                    )
                    boundary_cells += 4
    figures_checked = []
    for path in sorted(figures.glob("*.png")):
        with Image.open(path) as im:
            assert im.width == 2161
            assert im.height in (1771, 1889)
            assert all(abs(d - 300) < 0.1 for d in im.info["dpi"])
            im.verify()
        svg = ET.parse(path.with_suffix(".svg")).getroot()
        assert len(svg.findall(".//{http://www.w3.org/2000/svg}text")) > 0
        assert (
            json.loads(path.with_suffix(".alignment.json").read_text())["verdict"]
            == "PASS"
        )
        assert json.loads(path.with_suffix(".text_geometry.json").read_text())["passed"]
        figures_checked.append(path.name)
    assert len(figures_checked) == 16
    report = ROOT / "docs/ootang_tcn_results.v1.0.md"
    report_text = report.read_text()
    values = json.loads((root / "verification/report_values.json").read_text())
    for item in values:
        key = item["key"].split("/")
        if len(key) == 4 and key[-1] != "coverage90_percent":
            phase, name, h, metric = key
            actual = lookup.loc[(phase, name, int(h)), metric]
        elif len(key) == 4:
            phase, name, h, _ = key
            actual = 100 * lookup.loc[(phase, name, int(h)), "coverage90"]
        elif len(key) == 5:
            phase, name, h, point, _ = key
            actual = 100 * point_lookup.loc[(phase, name, int(h), point), "coverage90"]
        else:
            actual = sum(
                r["elapsed_seconds"]
                for r in json.loads((root / "fit_registry.json").read_text())
            )
        close(np.array(actual), np.array(item["value"]), 1e-10)
        assert item["display"] in report_text
    for doc in [report, ROOT / spec["figures_root"] / "README.md"]:
        for link in re.findall(r"\]\(([^)]+)\)", doc.read_text()):
            if not link.startswith(("http", "#")):
                assert (doc.parent / link).resolve().exists(), (doc, link)
    result = dict(
        passed=True,
        time_utc=now(),
        csv_groups=groups,
        csv_rows=len(curves),
        csv_max_difference_mm=maximum,
        comparison_rows=len(compare),
        working_condition_cells=gate_cells,
        unscored_issued_scale_cells=boundary_cells,
        max_unscored_scale_difference_mm=boundary_difference,
        report_numeric_values=len(values),
        figures_checked=figures_checked,
        no_new_training=True,
        script_sha256=sha(__file__),
        report_sha256=sha(report),
    )
    save_json(out, result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
