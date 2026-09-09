"""Independent scoring joins observations only after prediction and selection."""

from pathlib import Path
import numpy as np
import pandas as pd
from .data import POINTS, COUNTS, phase_masks, mask_sha
from .probability import summarize, crps, interval_score

PROBABILITY_METRICS = ["crps"] + [
    f"{m}_{level}"
    for level in [80, 90, 95]
    for m in ["picp", "width", "interval_score"]
]


def export_stage(stage, drivers, baseline, models, observed, output, failed_routes=()):
    nfit, n = COUNTS[stage]
    dates = drivers.dates
    if len(dates) != n or observed.shape != (n, 4):
        raise ValueError("Scorer date/station dimensions mismatch")
    masks = phase_masks(dates, stage)
    prediction_frames, metric_rows = [], []
    physics_version = (
        "prefix_792_v1_1" if stage == "development" else "advisor_frozen_1168"
    )
    all_models = {"M0": (baseline[None], None), **models}
    for model, (means, sigmas) in all_models.items():
        components = (
            ["deterministic"]
            if model == "M0"
            else ["seed_0", "seed_1", "seed_2", "mixture"]
        )
        for component in components:
            if model == "M0":
                mu, sd, q = means[0], np.full((n, 4), np.nan), {}
                component_means = component_sigmas = None
            else:
                idx = (
                    slice(None)
                    if component == "mixture"
                    else slice(int(component[-1]), int(component[-1]) + 1)
                )
                component_means, component_sigmas = means[idx], sigmas[idx]
                stats = summarize(component_means[:, 30:], component_sigmas[:, 30:])
                mu = component_means.mean(axis=0)
                sd = np.vstack([np.full((30, 4), np.nan), stats["std"]])
                q = {
                    k: np.vstack([np.full((30, 4), np.nan), v])
                    for k, v in stats.items()
                    if k.startswith(("lower", "upper"))
                }
            frame = pd.DataFrame(
                dict(
                    stage=stage,
                    model=model,
                    component=component,
                    date=np.repeat(dates.strftime("%Y-%m-%d"), 4),
                    station=np.tile(POINTS, n),
                    physics_version=physics_version,
                    phase=np.repeat(
                        np.where(np.arange(n) < nfit, "train", "prediction"), 4
                    ),
                    status=np.repeat(np.where(np.arange(n) < 30, "warmup", "valid"), 4),
                    mean_mm=mu.ravel(),
                    std_mm=sd.ravel(),
                )
            )
            for level in [80, 90, 95]:
                for side in ["lower", "upper"]:
                    frame[f"{side}_{level}_mm"] = q.get(
                        f"{side}_{level}", np.full((n, 4), np.nan)
                    ).ravel()
            prediction_frames.append(frame)
            for phase, mask in masks.items():
                residual = mu[mask] - observed[mask]
                metrics = dict(
                    rmse=np.sqrt(np.mean(residual**2, axis=0)),
                    mae=np.mean(abs(residual), axis=0),
                )
                if sigmas is not None:
                    metrics["crps"] = crps(
                        component_means[:, mask],
                        component_sigmas[:, mask],
                        observed[mask],
                    ).mean(axis=0)
                    for level in [80, 90, 95]:
                        low, high = q[f"lower_{level}"][mask], q[f"upper_{level}"][mask]
                        metrics[f"picp_{level}"] = (
                            (low <= observed[mask]) & (observed[mask] <= high)
                        ).mean(axis=0)
                        metrics[f"width_{level}"] = (high - low).mean(axis=0)
                        metrics[f"interval_score_{level}"] = interval_score(
                            observed[mask], low, high, level
                        ).mean(axis=0)
                else:
                    metrics.update(
                        {name: np.full(4, np.nan) for name in PROBABILITY_METRICS}
                    )
                common = dict(
                    stage=stage,
                    model=model,
                    component=component,
                    phase=phase,
                    valid_days=int(mask.sum()),
                    mask_sha256=mask_sha(dates, mask),
                )
                for metric, values in metrics.items():
                    for j, point in enumerate([*POINTS, "four_point_mean"]):
                        value = values[j] if j < 4 else np.mean(values)
                        metric_rows.append(
                            dict(
                                **common,
                                station=point,
                                metric=metric,
                                value=value,
                                unit="fraction" if metric.startswith("picp") else "mm",
                                status="not_applicable" if np.isnan(value) else "valid",
                            )
                        )
                metric_rows.append(
                    dict(
                        **common,
                        station="pooled",
                        metric="rmse",
                        value=float(np.sqrt(np.mean(residual**2))),
                        unit="mm",
                        status="valid",
                    )
                )
    for model in failed_routes:
        for component in ["seed_0", "seed_1", "seed_2", "mixture"]:
            frame = prediction_frames[0].copy()
            frame["model"], frame["component"], frame["status"] = (
                model,
                component,
                "failed",
            )
            for column in ["mean_mm", "std_mm"] + [
                f"{side}_{level}_mm"
                for side in ["lower", "upper"]
                for level in [80, 90, 95]
            ]:
                frame[column] = np.nan
            prediction_frames.append(frame)
            for phase, mask in masks.items():
                for station in [*POINTS, "four_point_mean"]:
                    for metric in ["rmse", "mae", *PROBABILITY_METRICS]:
                        metric_rows.append(
                            dict(
                                stage=stage,
                                model=model,
                                component=component,
                                phase=phase,
                                station=station,
                                metric=metric,
                                value=np.nan,
                                unit="fraction" if metric.startswith("picp") else "mm",
                                status="failed",
                                valid_days=0,
                                mask_sha256=mask_sha(dates, mask),
                            )
                        )
    predictions, metrics = (
        pd.concat(prediction_frames, ignore_index=True),
        pd.DataFrame(metric_rows),
    )
    if predictions.duplicated(["stage", "model", "component", "date", "station"]).any():
        raise ValueError("Duplicate prediction keys")
    predictions.to_csv(output / f"predictions_{stage}.csv", index=False)
    metrics.to_csv(output / f"metrics_{stage}.csv", index=False)
    return predictions, metrics


def acceptance(metrics):
    result = {}
    for model in ("M1", "M2"):
        flags = []
        for station in POINTS:
            differences = {}
            for phase in ("train", "prediction"):
                for metric in ("rmse", "mae"):
                    b = metrics[
                        (metrics.model == "M0")
                        & (metrics.station == station)
                        & (metrics.phase == phase)
                        & (metrics.metric == metric)
                    ]
                    m = metrics[
                        (metrics.model == model)
                        & (metrics.component == "mixture")
                        & (metrics.station == station)
                        & (metrics.phase == phase)
                        & (metrics.metric == metric)
                    ]
                    if len(m) != 1 or len(b) != 1:
                        differences[f"{phase}_{metric}_delta_mm"] = None
                    else:
                        differences[f"{phase}_{metric}_delta_mm"] = float(
                            m.value.iloc[0] - b.value.iloc[0]
                        )
            satisfied = all(v is not None and v < -1e-6 for v in differences.values())
            flags.append(
                dict(
                    station=station,
                    mean_goal_satisfied=satisfied,
                    differences=differences,
                )
            )
        result[model] = dict(
            points=flags,
            satisfied_points=sum(x["mean_goal_satisfied"] for x in flags),
            denominator=4,
            user_acceptance="pending",
            coverage_target_is_nominal=True,
        )
    return result


def plot_results(predictions, observed, drivers, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    colors = {"M0": "#616161", "M1": "#2267b1", "M2": "#ce6b27"}
    dates = drivers.dates
    for j, point in enumerate(POINTS):
        fig, axes = plt.subplots(3, 1, figsize=(11, 9), layout="constrained")
        for ax in axes[:2]:
            ax.plot(dates, observed[:, j], color="black", lw=1.2, label="Observed")
        axes[2].axhline(0, color="black", lw=0.7)
        for model in ("M0", "M1", "M2"):
            frame = predictions[
                (predictions.station == point)
                & (predictions.model == model)
                & (
                    predictions.component
                    == ("deterministic" if model == "M0" else "mixture")
                )
            ].sort_values("date")
            if frame.empty:
                continue
            mu = frame.mean_mm.to_numpy()
            for ax in axes[:2]:
                ax.plot(dates, mu, color=colors[model], lw=1, label=model)
                if model != "M0":
                    ax.fill_between(
                        dates,
                        frame.lower_90_mm,
                        frame.upper_90_mm,
                        color=colors[model],
                        alpha=0.15,
                    )
            axes[2].plot(
                dates, mu - observed[:, j], color=colors[model], lw=1, label=model
            )
            if model != "M0":
                axes[2].fill_between(
                    dates,
                    frame.lower_90_mm - observed[:, j],
                    frame.upper_90_mm - observed[:, j],
                    color=colors[model],
                    alpha=0.12,
                )
        axes[0].axvspan(
            dates[0], dates[29], color="#d9d9d9", alpha=0.7, label="30-day warmup"
        )
        axes[0].set_title(
            f"{point} | continuous displacement and marginal 90% intervals", loc="left"
        )
        axes[1].set_title(
            "Historical prediction: observed daily rain and reservoir level supplied",
            loc="left",
            fontsize=10,
        )
        axes[2].set_title(
            "Historical prediction residuals: mean minus observation",
            loc="left",
            fontsize=10,
        )
        for ax in axes:
            ax.axvline(dates[1168], color="black", ls="--", lw=0.8)
            ax.set_ylabel("Displacement (mm)" if ax is not axes[2] else "Residual (mm)")
            ax.grid(alpha=0.18)
            ax.spines[["top", "right"]].set_visible(False)
        for ax in axes[1:]:
            ax.set_xlim(dates[1168], dates[-1])
        axes[0].legend(ncol=5, fontsize=9)
        fig.savefig(output / f"{point}_forecast.png", dpi=180)
        plt.close(fig)
