"""Rescore frozen 293-day outputs; never fit, infer, select, or truncate dates."""

import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "e48d3e1b08f596bd571475b9872735db65e33ac2"
BASE = Path("results/ootang_bplus_v1_1/20260910_implementation")
ZIP_PREFIX = "section2d_v4/work/delivery_final/outang_repro/"
POINTS = ["ATU1", "ATU5", "MJ3", "MJ1"]
LEVELS = (80, 90, 95)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def max_difference(a, b):
    require(a.shape == b.shape, "Array shape mismatch")
    require(np.isfinite(a).all() and np.isfinite(b).all(), "Nonfinite scored array")
    return float(np.max(np.abs(a - b)))


def read_inputs():
    sources, files = [], {}

    def frozen(path):
        raw = (ROOT / path).read_bytes()
        original = subprocess.check_output(
            ["git", "show", f"{SOURCE_COMMIT}:{path}"], cwd=ROOT
        )
        require(raw == original, f"Source differs from frozen commit: {path}")
        sources.append(
            dict(
                path=str(path),
                sha256=sha(raw),
                bytes=len(raw),
                verification="git byte equality",
            )
        )
        return raw

    manifest = json.loads(frozen(BASE / "artifact_manifest.json"))["files"]
    names = [
        "reference_provenance.json",
        "frozen_reference.npz",
        "selection.json",
        "metrics_final.csv",
        "predictions_final.csv",
        "source_snapshot/code/physics_guided/probability.py",
    ]
    names += [
        f"final/{m}/{n}"
        for m in ("M1", "M2")
        for n in ("selection.json", "selected_predictions.npz")
    ]
    for name in names:
        files[name] = frozen(BASE / name)
        require(
            sha(files[name]) == manifest[name]["sha256"], f"Manifest mismatch: {name}"
        )
    monitoring = frozen(Path("data/monitoring_data.csv"))
    archive = frozen(Path("section2d_v4.zip"))
    provenance = json.loads(files["reference_provenance.json"])
    require(sha(archive) == provenance["archive_sha256"], "Archive hash mismatch")
    with ZipFile(io.BytesIO(archive)) as z:
        for name, expected in provenance["source_hashes"].items():
            raw = z.read(ZIP_PREFIX + name)
            require(sha(raw) == expected, f"ZIP member hash mismatch: {name}")
            sources.append(
                dict(
                    path=f"section2d_v4.zip!{ZIP_PREFIX}{name}",
                    sha256=sha(raw),
                    bytes=len(raw),
                    verification="frozen archive and provenance hash",
                )
            )

        def read(name):
            return z.read(ZIP_PREFIX + "section2d_v4/" + name)

        parameters = json.loads(read("results/calibrated.json"))
        protocol = json.loads(read("results/protocol.json"))
        with np.load(io.BytesIO(read("results/curves.npz"))) as a:
            original = {k: a[k] for k in ("observed", "predicted", "dates")}
        future = pd.read_csv(io.BytesIO(read("output/future_prediction.csv")))
    return files, monitoring, parameters, protocol, original, future, sources


def audit():
    files, monitoring, parameters, protocol, original, future, sources = read_inputs()
    data = pd.read_csv(io.BytesIO(monitoring))
    dates = pd.DatetimeIndex(pd.to_datetime(data.Date))
    require(dates.equals(pd.date_range("2016-07-01", "2020-06-30")), "Date mismatch")
    require(protocol["fit_days"] == parameters["fit_days"] == 1168, "Fit mismatch")
    require(protocol["prediction_days"] == 293, "Prediction length mismatch")
    require(
        protocol["points"] == parameters["points"] == POINTS, "Point order mismatch"
    )
    require(
        protocol["fit_start"] == "2016-07-01"
        and protocol["fit_end"] == "2019-09-11"
        and protocol["prediction_start"] == "2019-09-12"
        and protocol["prediction_end"] == "2020-06-30",
        "Protocol dates mismatch",
    )
    require(
        np.array_equal(dates.to_numpy(), original["dates"]), "Original dates mismatch"
    )
    observed = data[[p + "/mm" for p in POINTS]].to_numpy(float)
    with np.load(io.BytesIO(files["frozen_reference.npz"])) as a:
        baseline = a["mu"]
    differences = {
        "observations_vs_original_curves_mm": max_difference(
            observed, original["observed"]
        ),
        "bplus_vs_original_curves_mm": max_difference(baseline, original["predicted"]),
    }
    require(max(differences.values()) < 1e-9, "Observation or B+ source mismatch")
    require(
        pd.DatetimeIndex(pd.to_datetime(future.Date)).equals(dates[1168:]),
        "Original forecast CSV dates mismatch",
    )
    differences["bplus_vs_original_forecast_csv_mm"] = max_difference(
        baseline[1168:], future[[p + "_predicted_mm" for p in POINTS]].to_numpy(float)
    )
    require(
        differences["bplus_vs_original_forecast_csv_mm"] < 1e-9,
        "Original forecast CSV values mismatch",
    )

    # Only this hash-verified, pure scoring module is imported; no model loader.
    spec = importlib.util.spec_from_file_location(
        "frozen_probability",
        ROOT / BASE / "source_snapshot/code/physics_guided/probability.py",
    )
    probability = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probability)
    saved_metrics = pd.read_csv(io.BytesIO(files["metrics_final.csv"]))
    saved_predictions = pd.read_csv(io.BytesIO(files["predictions_final.csv"]))
    selection = json.loads(files["selection.json"])
    require(selection["frozen_before_final"] is True, "Selection was not frozen")
    window = dates[1168:]
    y = observed[1168:]
    mask_hash = sha("\n".join(window.strftime("%Y-%m-%d")).encode())
    metrics, daily, model_records = [], [], {}
    metric_error, prediction_error = 0.0, 0.0
    for model in ("M0", "M1", "M2"):
        component = "deterministic" if model == "M0" else "mixture"
        stats = None
        mu = baseline[1168:]
        values = {}
        if model != "M0":
            final_selection = json.loads(files[f"final/{model}/selection.json"])
            for record in (selection["routes"][model], final_selection):
                require(
                    record["e_mu"] == record["e_sigma"] == 0
                    and record["seeds"] == [0, 1, 2],
                    f"Unexpected selection: {model}",
                )
            with np.load(
                io.BytesIO(files[f"final/{model}/selected_predictions.npz"])
            ) as a:
                all_means, all_sigmas = a["means"], a["sigmas"]
            require(
                all_means.shape == all_sigmas.shape == (3, 1461, 4),
                "Model axes mismatch",
            )
            require(np.isfinite(all_means[:, 30:]).all(), "Nonfinite valid model mean")
            means, sigmas = all_means[:, 1168:], all_sigmas[:, 1168:]
            stats = probability.summarize(means, sigmas)
            mu = stats["mean"]
            values["crps"] = probability.crps(means, sigmas, y)
            model_records[model] = dict(
                e_mu=0,
                e_sigma=0,
                seeds=[0, 1, 2],
                prediction_mean_vs_bplus_max_mm=float(
                    np.max(abs(means - baseline[None, 1168:]))
                ),
                valid_mean_vs_bplus_max_mm=float(
                    np.max(abs(all_means[:, 30:] - baseline[None, 30:]))
                ),
                seed_mean_spread_mm=float(np.max(np.ptp(means, axis=0))),
                sigma_min_mm=float(sigmas.min()),
                sigma_max_mm=float(sigmas.max()),
                excluded_warmup_nonfinite_means=int(
                    (~np.isfinite(all_means[:, :30])).sum()
                ),
            )
            for level in LEVELS:
                lower, upper = stats[f"lower_{level}"], stats[f"upper_{level}"]
                values[f"picp_{level}"] = ((lower <= y) & (y <= upper)).astype(float)
                values[f"width_{level}"] = upper - lower
                values[f"interval_score_{level}"] = probability.interval_score(
                    y, lower, upper, level
                )
        error = mu - y
        point_metrics = {
            "rmse": np.sqrt(np.mean(error**2, axis=0)),
            "mae": np.mean(abs(error), axis=0),
        }
        point_metrics.update({k: v.mean(axis=0) for k, v in values.items()})
        if model == "M0":
            probability_names = ["crps"] + [
                f"{m}_{level}"
                for level in LEVELS
                for m in ("picp", "width", "interval_score")
            ]
            point_metrics.update({k: np.full(4, np.nan) for k in probability_names})
        stored = saved_metrics[
            (saved_metrics.model == model)
            & (saved_metrics.component == component)
            & (saved_metrics.phase == "prediction")
        ]
        require(
            set(stored.valid_days) == {293} and set(stored.mask_sha256) == {mask_hash},
            f"Scoring mask differs: {model}",
        )
        for metric, v in point_metrics.items():
            for station, value in zip(POINTS + ["four_point_mean"], [*v, v.mean()]):
                row = stored[(stored.station == station) & (stored.metric == metric)]
                require(len(row) == 1, "Missing or duplicate saved metric")
                if np.isnan(value):
                    require(
                        row.value.isna().all()
                        and row.status.iloc[0] == "not_applicable",
                        "M0 must not acquire probability scores",
                    )
                else:
                    metric_error = max(
                        metric_error, abs(float(row.value.iloc[0]) - value)
                    )
                metrics.append(
                    dict(
                        model=model,
                        component=component,
                        station=station,
                        metric=metric,
                        value=value,
                        valid_days=293,
                        mask_sha256=mask_hash,
                        unit="fraction" if metric.startswith("picp") else "mm",
                        status="not_applicable" if np.isnan(value) else "valid",
                    )
                )
        pooled = float(np.sqrt(np.mean(error**2)))
        saved_pooled = stored[(stored.station == "pooled") & (stored.metric == "rmse")]
        require(len(saved_pooled) == 1, "Missing pooled RMSE")
        metric_error = max(
            metric_error, abs(float(saved_pooled.value.iloc[0]) - pooled)
        )
        metrics.append(
            dict(
                model=model,
                component=component,
                station="pooled",
                metric="rmse",
                value=pooled,
                valid_days=293,
                mask_sha256=mask_hash,
                unit="mm",
                status="valid",
            )
        )
        frame = pd.DataFrame(
            dict(
                model=model,
                component=component,
                date=np.repeat(window.strftime("%Y-%m-%d"), 4),
                station=np.tile(POINTS, 293),
                observed_mm=y.ravel(),
                mean_mm=mu.ravel(),
                error_mm=error.ravel(),
                abs_error_mm=abs(error).ravel(),
                squared_error_mm2=(error**2).ravel(),
            )
        )
        expected = saved_predictions[
            (saved_predictions.model == model)
            & (saved_predictions.component == component)
            & (saved_predictions.phase == "prediction")
        ]
        require(
            len(expected) == len(frame)
            and np.array_equal(expected.date, frame.date)
            and np.array_equal(expected.station, frame.station),
            "Prediction keys mismatch",
        )
        require(
            (expected.physics_version == "advisor_frozen_1168").all()
            and (expected.status == "valid").all(),
            "Prediction provenance mismatch",
        )
        prediction_error = max(
            prediction_error,
            max_difference(expected.mean_mm.to_numpy(), frame.mean_mm.to_numpy()),
        )
        if stats:
            frame["std_mm"] = stats["std"].ravel()
            frame["crps_mm"] = values["crps"].ravel()
            for level in LEVELS:
                for side in ("lower", "upper"):
                    column = f"{side}_{level}_mm"
                    frame[column] = stats[f"{side}_{level}"].ravel()
                    prediction_error = max(
                        prediction_error,
                        max_difference(
                            expected[column].to_numpy(), frame[column].to_numpy()
                        ),
                    )
                for key in ("picp", "width", "interval_score"):
                    frame[f"{key}_{level}"] = values[f"{key}_{level}"].ravel()
        daily.append(frame)
    require(
        metric_error < 1e-9 and prediction_error < 1e-6, "Saved score/quantile mismatch"
    )
    initial_sigma = float(
        np.sqrt(np.mean((baseline[30:1168] - observed[30:1168]) ** 2))
    )
    require(
        all(
            r["valid_mean_vs_bplus_max_mm"] < 1e-9
            and r["sigma_min_mm"] == r["sigma_max_mm"]
            and abs(r["sigma_min_mm"] - initial_sigma) < 1e-9
            and r["seed_mean_spread_mm"] == 0
            for r in model_records.values()
        ),
        "Selected outputs no longer match the reported zero-correction case",
    )
    receipt = dict(
        source_commit=SOURCE_COMMIT,
        status="verified_saved_output_rescoring",
        scope="full 293-day historical forecast; no fitting, inference, reselection, or segmentation",
        protocol=protocol,
        fit_score_days_after_original_warmup=1138,
        initial_sigma_from_fit_residuals_mm=initial_sigma,
        mask_sha256=mask_hash,
        sources=sources,
        source_differences=differences,
        selected_models=model_records,
        max_metric_difference=metric_error,
        max_prediction_or_quantile_difference_mm=prediction_error,
        interpretation=dict(
            neural_mean_gain=False,
            probability_gain_vs_m0="not_applicable",
            tail_start_dates=None,
            segmented_scores=None,
            new_acceptance_thresholds=None,
            new_training_authorized=False,
        ),
        environment=dict(
            python=sys.version.split()[0], numpy=np.__version__, pandas=pd.__version__
        ),
        audit_script_sha256=sha(Path(__file__).read_bytes()),
    )
    return receipt, pd.DataFrame(metrics), pd.concat(daily, ignore_index=True)


def plot(daily, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    for residual in (False, True):
        fig, axes = plt.subplots(2, 2, figsize=(11, 7), layout="constrained")
        for station, ax in zip(POINTS, axes.flat):
            part = daily[(daily.station == station) & (daily.model == "M1")]
            dates = pd.to_datetime(part.date)
            offset = part.observed_mm if residual else 0.0
            ax.fill_between(
                dates,
                part.lower_90_mm - offset,
                part.upper_90_mm - offset,
                color="#2a7694",
                alpha=0.18,
                label="M1/M2 marginal 90% interval",
            )
            ax.plot(
                dates,
                part.mean_mm - offset,
                color="#206481",
                lw=1.8,
                label="B+ = M1 = M2 mean (within rounding)",
            )
            if residual:
                ax.axhline(0, color="#222222", lw=1)
            else:
                ax.plot(
                    dates, part.observed_mm, color="#222222", lw=1.6, label="Observed"
                )
            ax.set_title(station, loc="left", fontweight="bold")
            ax.set_ylabel(
                "Mean minus observation (mm)" if residual else "Displacement (mm)"
            )
            ax.set_xlim(dates.iloc[0], dates.iloc[-1])
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
            ax.grid(axis="y", alpha=0.2)
            ax.spines[["top", "right"]].set_visible(False)
        fig.suptitle("2019-09-12 to 2020-06-30 | all 293 days retained", fontsize=14)
        handles, labels = axes.flat[0].get_legend_handles_labels()
        fig.legend(
            handles,
            labels,
            loc="outside lower center",
            ncol=1,
            frameon=False,
            fontsize=9,
        )
        fig.savefig(
            output / ("daily_error.png" if residual else "forecast.png"),
            dpi=180,
            bbox_inches="tight",
            pad_inches=0.12,
        )
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument(
        "--output", type=Path, help="New directory; existing paths are refused"
    )
    args = parser.parse_args()
    if args.output is not None:
        require(
            not args.output.exists(),
            "Output directory already exists; refusing overwrite",
        )
    receipt, metrics, daily = audit()
    if args.output is not None:
        args.output.mkdir(parents=True, exist_ok=False)
        metrics.to_csv(args.output / "metrics.csv", index=False)
        daily.to_csv(args.output / "daily_predictions.csv", index=False)
        pd.DataFrame(receipt["sources"]).to_csv(
            args.output / "sources.csv", index=False
        )
        plot(daily, args.output)
        receipt["output_hashes"] = {
            p.name: sha(p.read_bytes()) for p in sorted(args.output.iterdir())
        }
        (args.output / "audit.json").write_text(
            json.dumps(receipt, indent=2, allow_nan=False) + "\n"
        )
    print(
        json.dumps(
            dict(
                status=receipt["status"],
                source_commit=SOURCE_COMMIT,
                scored_days=293,
                metrics=len(metrics),
                daily_rows=len(daily),
                max_metric_difference=receipt["max_metric_difference"],
                output=str(args.output) if args.output else None,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
