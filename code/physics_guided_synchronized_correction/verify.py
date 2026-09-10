"""Independent bounded rollout and probability arithmetic; no optimization."""

import argparse
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.stats import norm
import torch

from physics_guided.features import Scaler
from physics_guided_forecast_error.artifacts import (
    ROOT,
    array_sha,
    check_hashes,
    check_index,
    load_observations,
    read_json,
    sha,
)
from physics_guided_sample_learning.core import FLOOR, new_model
from .core import PREFIXES, CUTOFFS, POINTS, read_teacher


def numpy_limit(raw, amplitude, rate):
    result = np.empty_like(raw)
    state = np.zeros(4)
    for t in range(len(raw)):
        desired = amplitude * np.tanh(raw[t] / amplitude)
        state = state + rate * np.tanh((desired - state) / rate)
        result[t] = state
    return result


def independent_crps(means, sigma, labels):
    scale = sigma[:, None]
    delta = labels[None] - means
    z = delta / scale
    first = np.mean(delta * (2 * norm.cdf(z) - 1) + 2 * scale * norm.pdf(z), axis=0)
    distance = means[:, None] - means[None, :]
    combined = np.sqrt(scale[:, None] ** 2 + scale[None, :] ** 2)
    z = distance / combined
    return first - 0.5 * np.mean(
        distance * (2 * norm.cdf(z) - 1) + 2 * combined * norm.pdf(z), axis=(0, 1)
    )


def verify(out):
    manifest = read_json(out / "manifest.json")
    spec = manifest["specification"]
    physical, learning = ROOT / spec["physical_source"], ROOT / spec["learning_source"]
    protected = read_json(out / "protected_before.json")
    check_hashes(protected)
    check_hashes(manifest["sources"])
    check_hashes(manifest["sources"], out / "sources")
    for source in (physical, learning, ROOT / spec["diagnostic_source"]):
        check_index(source)
    if (out / "artifact_manifest.json").exists():
        check_index(out)
    if sha(out / "input_prefix_792.csv") != manifest["input_sha256"] or manifest[
        "input_sha256"
    ] != sha(physical / "input_prefix_792.csv"):
        raise ValueError("Input snapshot changed")
    for relative, expected in (
        (
            "config/ootang_bplus_synchronized_correction.v1_7.json",
            manifest["config_sha256"],
        ),
        (spec["protocol"], manifest["plan_sha256"]),
    ):
        if (
            sha(out / "sources" / relative) != expected
            or sha(ROOT / relative) != expected
        ):
            raise ValueError("Frozen protocol snapshot differs")
    logs = pd.read_csv(out / "training.csv")
    if (
        len(logs) != 1800
        or logs.duplicated(["fit_days", "strategy", "seed", "epoch"]).any()
        or not np.isfinite(
            logs[["loss_before_update", "gradient_norm", "elapsed_seconds"]]
        )
        .all()
        .all()
    ):
        raise ValueError("Training log inventory or values differ")
    expected_checkpoints = {
        f"model_{h}_{s}_{k}/e{e}.pt"
        for h in PREFIXES
        for s in ("U", "L")
        for k in range(3)
        for e in (0, 25, 50, 75, 100)
    }
    if {
        str(p.relative_to(out)) for p in out.glob("model_*/*.pt")
    } != expected_checkpoints:
        raise ValueError("Checkpoint inventory differs")
    locks = [read_json(out / f"mean_lock_{h}.json") for h in PREFIXES]
    mean_lock = read_json(out / "mean_lock.json")
    if len(mean_lock["files"]) != 3:
        raise ValueError("Prefix lock inventory differs")
    check_hashes(mean_lock["files"], out)
    last_time = datetime.fromisoformat(manifest["started_utc"])
    for h, lock in zip(PREFIXES, locks):
        check_hashes(lock["files"], out)
        if lock["fit_days"] != h or len(lock["files"]) != 35:
            raise ValueError("Incomplete prefix lock")
        constants = read_json(out / f"constants_{h}.json")
        read_time = datetime.fromisoformat(constants["label_read_utc"])
        lock_time = datetime.fromisoformat(lock["locked_utc"])
        if not last_time <= read_time <= lock_time:
            raise ValueError("Later labels preceded an earlier forecast lock")
        earlier = list(PREFIXES).index(h) - 1
        expected = (
            sha(out / f"mean_lock_{list(PREFIXES)[earlier]}.json")
            if earlier >= 0
            else None
        )
        if constants["prior_lock_sha256"] != expected:
            raise ValueError("Prefix lock chain changed")
        last_time = lock_time
    if last_time > datetime.fromisoformat(mean_lock["locked_utc"]):
        raise ValueError("Global mean lock predates a prefix lock")
    prediction_lock = read_json(out / "prediction_lock.json")
    if len(prediction_lock["files"]) != 18:
        raise ValueError("Incomplete prediction/calibration lock")
    check_hashes(prediction_lock["files"], out)
    scoring = read_json(out / "scoring.json")
    if scoring["prediction_lock_sha256"] != sha(
        out / "prediction_lock.json"
    ) or datetime.fromisoformat(scoring["label_read_utc"]) < datetime.fromisoformat(
        prediction_lock["locked_utc"]
    ):
        raise ValueError("Scoring preceded final prediction lock")
    teachers = read_json(out / "teachers.json")
    previous_teachers = read_json(learning / "teachers.json")
    if teachers != {str(h): previous_teachers[str(h)] for h in PREFIXES}:
        raise ValueError("Physical teacher record changed")
    _, _, labels = load_observations(out / "input_prefix_792.csv", 792)
    values_checked, max_difference = 0, 0.0

    def close(actual, expected, atol=1e-8):
        nonlocal values_checked, max_difference
        a, b = np.asarray(actual), np.asarray(expected)
        if a.shape != b.shape or not np.allclose(
            a, b, rtol=0, atol=atol, equal_nan=True
        ):
            raise ArithmeticError("Independent numerical replay differs")
        finite = np.isfinite(a) & np.isfinite(b)
        if finite.any():
            max_difference = max(
                max_difference, float(abs(a[finite] - b[finite]).max())
            )
        values_checked += a.size

    forecasts = {}
    diagnostics = read_json(out / "bound_diagnostics.json")
    if set(diagnostics) != {
        f"{h}_{s}_{k}" for h in PREFIXES for s in ("U", "L") for k in range(3)
    }:
        raise ValueError("Bound diagnostics incomplete")
    for h in PREFIXES:
        base, features = read_teacher(out / "input_prefix_792.csv", physical, h)
        constants = read_json(out / f"constants_{h}.json")
        info = read_json(out / f"scaler_{h}.json")
        if (
            constants["labels_sha256"] != array_sha(labels[:h])
            or constants["fit_days"] != h
            or info["fit_days"] != h
        ):
            raise ValueError("Training label prefix differs")
        e = labels[:h] - base[:h]
        a = np.maximum(1, 2 * np.linalg.norm(e[30:], axis=0) / np.sqrt(h - 30))
        d = np.maximum(
            0.01, 2 * np.linalg.norm(e[30:] - e[29 : h - 1], axis=0) / np.sqrt(h - 30)
        )
        close(constants["amplitude_mm"], a)
        close(constants["rate_mm_per_day"], d)
        average, scale = (
            features[:h].mean(axis=0),
            np.maximum(features[:h].std(axis=0), FLOOR),
        )
        average[-4:], scale[-4:] = 0, 1
        close(info["mean"], average)
        close(info["scale"], scale)
        close(info["floor"], np.broadcast_to(FLOOR, average.shape))
        scaler = Scaler(*[np.asarray(info[k]) for k in ("mean", "scale", "floor")])
        x = scaler.transform(features)
        for strategy in ("P0", "U", "L"):
            with np.load(out / f"mean_{h}_{strategy}.npz") as saved:
                means, raw_saved = saved["means"], saved["raw"]
            if strategy == "P0":
                close(means, base[None])
                close(raw_saved, np.zeros_like(base[None]))
            else:
                expected_means, raw_expected = [], []
                for seed in range(3):
                    group = logs[
                        (logs.fit_days == h)
                        & (logs.strategy == strategy)
                        & (logs.seed == seed)
                    ]
                    if list(group.epoch) != list(range(1, 101)):
                        raise ValueError("Missing epoch or unexpected retry")
                    model = new_model(seed)
                    directory = out / f"model_{h}_{strategy}_{seed}"
                    initial = torch.load(directory / "e0.pt", weights_only=True)
                    if any(
                        not torch.equal(value, initial[key])
                        for key, value in model.state_dict().items()
                    ):
                        raise ValueError("Paired zero initialization differs")
                    model.load_state_dict(
                        torch.load(directory / "e100.pt", weights_only=True)
                    )
                    raw = np.full_like(base, np.nan)
                    raw[29] = 0
                    with torch.no_grad():
                        for start in range(30, len(base), 17):
                            end = min(start + 17, len(base))
                            batch = torch.as_tensor(
                                np.stack(
                                    [
                                        x[t - 29 : t + 1, :, None, :]
                                        for t in range(start, end)
                                    ]
                                )
                            )
                            raw[start:end] = model(batch).numpy()
                    residual = (
                        raw[30:] if strategy == "U" else numpy_limit(raw[30:], a, d)
                    )
                    expected = np.full_like(base, np.nan)
                    expected[29], expected[30:] = base[29], base[30:] + residual
                    expected_means.append(expected)
                    raw_expected.append(raw)
                    diag = diagnostics[f"{h}_{strategy}_{seed}"]
                    max_abs = np.max(abs(residual), axis=0)
                    max_step = np.max(
                        abs(np.diff(np.vstack([np.zeros(4), residual]), axis=0)), axis=0
                    )
                    close(diag["max_abs_mm"], max_abs)
                    close(diag["max_step_mm"], max_step)
                    close(diag["amplitude_mm"], a)
                    close(diag["rate_mm_per_day"], d)
                    if strategy == "L" and (
                        (max_abs > a + 1e-8).any() or (max_step > d + 1e-8).any()
                    ):
                        raise ArithmeticError("Bounded rollout exceeded limits")
                close(means, np.stack(expected_means))
                close(raw_saved, np.stack(raw_expected))
            if not np.isfinite(means[:, 30:]).all():
                raise ArithmeticError("Nonfinite scored prediction")
            forecasts[h, strategy] = means
    metrics = pd.read_csv(out / "metrics.csv")
    seed_metrics = pd.read_csv(out / "seed_metrics.csv")
    for frame, count, keys in (
        (metrics, 48, ["outer_days", "strategy", "station", "part"]),
        (seed_metrics, 112, ["outer_days", "strategy", "station", "part", "component"]),
    ):
        if len(frame) != count or frame.duplicated(keys).any():
            raise ValueError("Scored metric inventory differs")

    def validate_row(row, m, s, y, lo_hi, atol=1e-8):
        error = m.mean(axis=0) - y
        close(
            [row.days, row.rmse_mm, row.mae_mm, row.crps_mm],
            [
                len(y),
                np.sqrt(np.mean(error**2)),
                abs(error).mean(),
                independent_crps(m, s, y).mean(),
            ],
        )
        for level, (lo, hi) in lo_hi.items():
            coverage = ((lo <= y) & (y <= hi)).mean()
            interval_score = (
                hi
                - lo
                + 2
                / (1 - level / 100)
                * (np.maximum(lo - y, 0) + np.maximum(y - hi, 0))
            )
            close(row[f"coverage_{level}"], coverage)
            close(
                [row[f"width_{level}_mm"], row[f"interval_score_{level}_mm"]],
                [(hi - lo).mean(), interval_score.mean()],
                atol=atol,
            )

    prior_metrics = pd.read_csv(learning / "metrics.csv")
    for n, c in CUTOFFS.items():
        for strategy in ("P0", "U", "L"):
            record = read_json(out / f"calibration_{n}_{strategy}.json")
            read_time = datetime.fromisoformat(record["label_read_utc"])
            if (
                record["origin"] != c
                or record["end"] != n
                or record["labels_sha256"] != array_sha(labels[c:n])
                or record["mean_lock_sha256"] != sha(out / f"mean_lock_{c}.json")
                or not datetime.fromisoformat(mean_lock["locked_utc"])
                <= read_time
                <= datetime.fromisoformat(prediction_lock["locked_utc"])
            ):
                raise ValueError("Calibration source or temporal isolation differs")
            expected_cal = forecasts[c, strategy][:, c:n]
            sigma = np.maximum(
                0.001, np.sqrt(np.mean((expected_cal - labels[None, c:n]) ** 2, axis=1))
            )
            with np.load(out / f"calibration_{n}_{strategy}.npz") as saved:
                close(saved["means"], expected_cal)
                close(saved["scales"], sigma)
            with np.load(out / f"prediction_{n}_{strategy}.npz") as saved:
                means = saved["means"]
                close(means, forecasts[n, strategy])
                close(saved["scales"], sigma)
            with np.load(out / f"distribution_{n}_{strategy}.npz") as saved:
                dist = {k: saved[k] for k in saved.files}
            average = means[:, 30:].mean(axis=0)
            close(dist["mean"], average)
            close(
                dist["std"],
                np.sqrt(
                    np.mean(
                        sigma[:, None] ** 2 + (means[:, 30:] - average) ** 2, axis=0
                    )
                ),
            )
            for level in (80, 90, 95):
                for side, p in (
                    ("lower", (1 - level / 100) / 2),
                    ("upper", (1 + level / 100) / 2),
                ):
                    cdf = norm.cdf(
                        (dist[f"{side}_{level}"] - means[:, 30:]) / sigma[:, None]
                    ).mean(axis=0)
                    close(
                        cdf,
                        np.full_like(cdf, p),
                        atol=1e-6 / (sigma.min() * np.sqrt(2 * np.pi)) + 1e-12,
                    )
            for part, start, end in (("train", 30, n), ("prediction", n, n + 180)):
                for j, station in enumerate(POINTS):
                    row = metrics[
                        (metrics.outer_days == n)
                        & (metrics.strategy == strategy)
                        & (metrics.station == station)
                        & (metrics.part == part)
                    ].iloc[0]
                    lo_hi = {
                        level: (
                            dist[f"lower_{level}"][start - 30 : end - 30, j],
                            dist[f"upper_{level}"][start - 30 : end - 30, j],
                        )
                        for level in (80, 90, 95)
                    }
                    validate_row(
                        row,
                        means[:, start:end, j],
                        sigma[:, j],
                        labels[start:end, j],
                        lo_hi,
                    )
                    for k in range(len(means)):
                        component = "deterministic" if strategy == "P0" else f"seed_{k}"
                        seed_row = seed_metrics[
                            (seed_metrics.outer_days == n)
                            & (seed_metrics.strategy == strategy)
                            & (seed_metrics.station == station)
                            & (seed_metrics.part == part)
                            & (seed_metrics.component == component)
                        ].iloc[0]
                        single = {
                            level: (
                                means[k, start:end, j]
                                + sigma[k, j] * norm.ppf((1 - level / 100) / 2),
                                means[k, start:end, j]
                                + sigma[k, j] * norm.ppf((1 + level / 100) / 2),
                            )
                            for level in (80, 90, 95)
                        }
                        # Closed-form quantiles differ from the saved bisection by <=1e-6 mm;
                        # interval scores amplify this boundary tolerance by at most 40.
                        validate_row(
                            seed_row,
                            means[k : k + 1, start:end, j],
                            sigma[k : k + 1, j],
                            labels[start:end, j],
                            single,
                            atol=4e-5,
                        )
                    if strategy == "P0":
                        previous = prior_metrics[
                            (prior_metrics.outer_days == n)
                            & (prior_metrics.strategy == "P0")
                            & (prior_metrics.station == station)
                            & (prior_metrics.part == part)
                        ].iloc[0]
                        for key in previous.index:
                            if key not in ("outer_days", "strategy", "station", "part"):
                                close(row[key], previous[key])
    comparisons = pd.read_csv(out / "comparisons.csv")
    if (
        len(comparisons) != 16
        or comparisons.duplicated(["outer_days", "strategy", "station"]).any()
    ):
        raise ValueError("Comparison inventory differs")
    for row in comparisons.itertuples():
        checks = []
        for part in ("train", "prediction"):
            chosen = metrics[
                (metrics.outer_days == row.outer_days)
                & (metrics.strategy == row.strategy)
                & (metrics.station == row.station)
                & (metrics.part == part)
            ].iloc[0]
            for reference in ("P0", "U"):
                base = metrics[
                    (metrics.outer_days == row.outer_days)
                    & (metrics.strategy == reference)
                    & (metrics.station == row.station)
                    & (metrics.part == part)
                ].iloc[0]
                for metric in ("rmse_mm", "mae_mm", "crps_mm"):
                    difference = chosen[metric] - base[metric]
                    close(
                        getattr(row, f"{part}_{metric}_minus_{reference}"), difference
                    )
                    if reference == "P0" and metric != "crps_mm":
                        checks.append(difference)
        if bool(row.strict_mean_improvement) != all(x < -1e-6 for x in checks):
            raise ArithmeticError("Strict acceptance differs")
    return dict(
        passed=True,
        checkpoint_replays=18,
        saved_checkpoints=90,
        training_updates=1800,
        metrics_rows=48,
        seed_metrics_rows=112,
        comparison_rows=16,
        bound_diagnostics=18,
        numeric_values_checked=values_checked,
        max_absolute_difference=max_difference,
        protected_files_checked=len(protected),
        source_files_checked=len(manifest["sources"]),
        new_physical_forwards=0,
        new_neural_updates=0,
        scope="Prefix locks, all scalers and bounds, paired initialization, checkpoint/raw-output replay, independent NumPy limiter, scales, CDF/CRPS/point metrics and strict acceptance; single-normal interval scores allow propagated 1e-6 mm quantile tolerance.",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    print(verify(ROOT / "results/ootang_bplus_v1_7" / args.run_id))
