"""Audit remaining saved CSV aggregates and actual SVG/PNG deliverables; no fits."""

import argparse
import json
import xml.etree.ElementTree as ET

from matplotlib import font_manager, ft2font
import numpy as np
import pandas as pd
from PIL import Image

from short_horizon.common import ROOT, now, save_json, sha
from short_horizon.data import observations
from .engine import read_spec
from .run import arrays, check_lock


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--attempt", default="v3")
    args = parser.parse_args()
    spec = read_spec(args.config)
    root = ROOT / spec["output_root"]
    out = root / "verification/delivery_receipt.json"
    if out.exists():
        raise FileExistsError(out)
    for phase in ["calibration", "forecast", "score"]:
        check_lock(root / phase)
    score = root / "score"
    metrics = pd.read_csv(score / "metrics_by_point.csv")
    summary = pd.read_csv(score / "summary.csv").set_index("model")
    train = arrays(root / "forecast/training_reconstruction.npz")
    means = arrays(root / "forecast/means.npz")
    scales = arrays(root / "forecast/sigma.npz")
    seeds = arrays(root / "forecast/seed_means.npz")
    training = pd.read_csv(score / "training_reconstruction.csv")
    training_metrics = pd.read_csv(score / "training_metrics.csv")
    seed_metrics = pd.read_csv(score / "seed_metrics.csv")
    forecasts = pd.read_csv(score / "forecasts.csv")
    obs = pd.read_csv(score / "observations.csv")
    y, _, dates = observations(spec, 1461)
    maximum = 0.0
    checked_values = 0

    def close(actual, expected):
        nonlocal maximum, checked_values
        actual, expected = np.asarray(actual), np.asarray(expected)
        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-8)
        maximum = max(maximum, float(np.max(np.abs(actual - expected))))
        checked_values += actual.size

    quantiles = {80: 1.2815515655446004, 90: 1.6448536269514722, 95: 1.959963984540054}
    value_cols = [
        c
        for c in metrics.columns
        if c not in {"model", "point", "n", "mean_complete", "probability_complete"}
    ]
    for model in spec["models"]:
        rows = metrics[metrics.model == model]
        assert len(rows) == 4 and summary.loc[model, "n_per_point"] == 293
        for key in value_cols:
            close(summary.loc[model, key], rows[key].mean())
        close(summary.loc[model, "pooled_rmse"], np.sqrt(np.mean(rows.rmse**2)))
        assert summary.loc[model, "mean_complete"]
        assert summary.loc[model, "probability_complete"]
        for p, point in enumerate(spec["points"]):
            row = training[(training.model == model) & (training.point == point)]
            assert len(row) == 916
            assert np.array_equal(row.date, dates[train["origins"]])
            close(row.mean_mm, train[model][:, p])
            err = train[model][:, p] - y[train["origins"], p]
            mr = training_metrics[
                (training_metrics.model == model) & (training_metrics.point == point)
            ].iloc[0]
            assert mr.n == 916
            close(mr.mae, np.mean(np.abs(err)))
            close(mr.rmse, np.sqrt(np.mean(err**2)))
            f = forecasts[(forecasts.model == model) & (forecasts.point == point)]
            assert len(f) == 293 and np.array_equal(f.date, dates[1168:])
            close(f.observed_mm, y[1168:, p])
            for level, q in quantiles.items():
                close(
                    f[f"lower{level}_mm"], means[model][:, p] - q * scales[model][:, p]
                )
                close(
                    f[f"upper{level}_mm"], means[model][:, p] + q * scales[model][:, p]
                )
    for p, point in enumerate(spec["points"]):
        row = obs[obs.point == point]
        assert len(row) == 1461 and np.array_equal(row.date, dates)
        close(row.observed_mm, y[:, p])
        close(row.display_offset_mm, np.full(1461, y[0, p]))
    for row in seed_metrics.itertuples():
        p = spec["points"].index(row.point)
        err = seeds[row.model][row.seed, :, p] - y[1168:, p]
        assert row.n == 293
        close(row.mae, np.mean(np.abs(err)))
        close(row.rmse, np.sqrt(np.mean(err**2)))

    figures = ROOT / spec["figures_root"] / args.attempt
    receipt = json.loads((figures / "receipt.json").read_text())
    assert receipt["script_sha256"] == sha(ROOT / "code/tcn_independent/figures.py")
    for name, digest in receipt["source_csv_sha256"].items():
        assert sha(score / name) == digest
    font_path = font_manager.findfont("Arial Unicode MS", fallback_to_default=False)
    cmap = ft2font.FT2Font(font_path).get_charmap()
    exports = []
    for record in receipt["figures"]:
        model = record["model"]
        align = json.loads((figures / f"{model}.alignment.json").read_text())
        geometry = json.loads((figures / f"{model}.text_geometry.json").read_text())
        assert align["verdict"] == "PASS" and geometry["passed"]
        with Image.open(figures / f"{model}.png") as im:
            assert im.size == (2161, 1511)
            assert all(abs(d - 300) < 0.1 for d in im.info["dpi"])
            assert min(im.getextrema()[0]) < max(im.getextrema()[0])
        tree = ET.parse(figures / f"{model}.svg")
        texts = tree.findall(".//{http://www.w3.org/2000/svg}text")
        # SVG emits two text elements for each two-line RMSE Text artist.
        assert len(texts) == geometry["text_count"] + len(record["panels"])
        content = "\n".join("".join(t.itertext()) for t in texts)
        missing = sorted({c for c in content if not c.isspace() and ord(c) not in cmap})
        assert not missing, missing
        for panel in record["panels"]:
            point = panel["point"]
            row = metrics[(metrics.model == model) & (metrics.point == point)].iloc[0]
            b = metrics[(metrics.model == "B_ANCHOR") & (metrics.point == point)].iloc[
                0
            ]
            close(panel["rmse_mm"], row.rmse)
            close(panel["baseline_rmse_mm"], b.rmse)
            assert f"预测 RMSE: {row.rmse:.2f} mm" in content
            assert f"B+ RMSE: {b.rmse:.2f} mm" in content
            assert panel["annotation"]["data_and_reference_strokes_clear"]
        exports.append(
            dict(
                model=model,
                panel_count=4,
                svg_editable_text_count=len(texts),
                png_sha256=sha(figures / f"{model}.png"),
                svg_sha256=sha(figures / f"{model}.svg"),
                glyphs_present=True,
                alignment="PASS",
                text_geometry="PASS",
            )
        )
    save_json(
        out,
        dict(
            passed=True,
            time_utc=now(),
            verifier_sha256=sha(__file__),
            checked_numeric_values=checked_values,
            maximum_difference_mm=maximum,
            forecast_rows=len(forecasts),
            training_rows=len(training),
            observation_rows=len(obs),
            seed_metric_rows=len(seed_metrics),
            model_summary_rows=len(summary),
            export_size_px=[2161, 1511],
            export_dpi=300,
            font_path=font_path,
            exports=exports,
            new_fits=0,
            optimizer_updates=0,
            visual_review="separate manual record required",
            generic_static_audit="17 PASS; two PDF-specific FAIL not applicable; two raster preference WARN accepted for requested PNG/SVG brief",
        ),
    )
    print(out.read_text())


if __name__ == "__main__":
    main()
