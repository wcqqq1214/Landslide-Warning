"""Replay locked checkpoints and independently recompute scores, without training."""

import argparse
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.stats import norm
import torch

from physics_guided.features import Scaler
from physics_guided_forecast_error.artifacts import (
    ROOT,
    check_hashes,
    check_index,
    load_observations,
    read_json,
    sha,
)
from .core import CUTOFFS, FLOOR, ORIGINS, POINTS, new_model, predict, teacher_arrays


def verify(out):
    manifest = read_json(out / "manifest.json")
    source = ROOT / manifest["specification"]["source_run"]
    protected = read_json(out / "protected_before.json")
    check_hashes(protected)
    check_hashes(manifest["sources"])
    check_hashes(manifest["sources"], out / "sources")
    check_index(source)
    if (out / "artifact_manifest.json").exists():
        check_index(out)
    if sha(out / "input_prefix_792.csv") != manifest["input_sha256"] or manifest[
        "input_sha256"
    ] != sha(source / "input_prefix_792.csv"):
        raise ValueError("Input snapshot differs")
    locks = [
        read_json(out / name)
        for name in ("mean_lock.json", "scale_lock.json", "prediction_lock.json")
    ]
    times = [datetime.fromisoformat(lock["locked_utc"]) for lock in locks]
    if (
        times != sorted(times)
        or locks[0]["updates"] != 1200
        or len(locks[0]["checkpoints"]) != 12
    ):
        raise ValueError("Model/scale/prediction locks or training budget differ")
    check_hashes(locks[0]["checkpoints"], out)
    check_hashes(locks[0]["scalers"], out)
    check_hashes(locks[1]["files"], out)
    check_hashes(locks[2]["files"], out)
    if locks[1]["mean_lock_sha256"] != sha(out / "mean_lock.json") or locks[2][
        "scale_lock_sha256"
    ] != sha(out / "scale_lock.json"):
        raise ValueError("Lock dependency changed")
    logs = pd.read_csv(out / "training.csv")
    if (
        len(logs) != 1200
        or logs.duplicated(["outer_days", "strategy", "seed", "epoch"]).any()
        or not np.isfinite(logs[["loss", "gradient_norm"]]).all().all()
    ):
        raise ValueError("Incomplete/nonfinite optimizer logs")
    if len(list(out.glob("mean_*/*.pt"))) != 60:
        raise ValueError("Incomplete checkpoint inventory")
    metrics = pd.read_csv(out / "metrics.csv")
    if (
        len(metrics) != 48
        or metrics.duplicated(["outer_days", "strategy", "station", "part"]).any()
    ):
        raise ValueError("Metric inventory differs")
    teachers = read_json(out / "teachers.json")
    source_records = read_json(source / "fitted_parameters_locked.json")["records"]
    for k, chosen in teachers.items():
        n = int(k)
        objectives = {
            r: source_records[f"{n}_{r}"]["record"]["objective"] for r in ("A", "B")
        }
        recipe = "A" if objectives["A"] <= objectives["B"] + 1e-12 else "B"
        if chosen != dict(
            recipe=recipe, record=source_records[f"{n}_{recipe}"]["record"]
        ):
            raise ValueError("Teacher selection/source differs")
    _, forcing, labels = load_observations(out / "input_prefix_792.csv", 792)
    max_difference, values_checked = 0.0, 0

    def close(actual, expected, atol=1e-8):
        nonlocal max_difference, values_checked
        a, b = np.asarray(actual), np.asarray(expected)
        if a.shape != b.shape or not np.allclose(
            a, b, rtol=1e-10, atol=atol, equal_nan=True
        ):
            raise ArithmeticError("Checkpoint or independent arithmetic differs")
        finite = np.isfinite(a) & np.isfinite(b)
        if finite.any():
            max_difference = max(
                max_difference, float(np.max(abs(a[finite] - b[finite])))
            )
        values_checked += a.size

    def independent_crps(m, s, y):
        z = (y[None] - m) / s
        first = np.mean(
            (y[None] - m) * (2 * norm.cdf(z) - 1) + 2 * s * norm.pdf(z), axis=0
        )
        d = m[:, None] - m[None, :]
        scale = np.sqrt(s[:, None] ** 2 + s[None, :] ** 2)
        z = d / scale
        return first - 0.5 * np.mean(
            d * (2 * norm.cdf(z) - 1) + 2 * scale * norm.pdf(z), axis=(0, 1)
        )

    for n, c in CUTOFFS.items():
        base_c, features_c = teacher_arrays(
            source, c, teachers[str(c)]["recipe"], forcing[:n], labels[0]
        )
        info = read_json(out / f"scaler_{n}.json")
        average, scale = (
            features_c[:c].mean(axis=0),
            np.maximum(features_c[:c].std(axis=0), FLOOR),
        )
        average[-4:], scale[-4:] = 0, 1
        close(info["mean"], average)
        close(info["scale"], scale)
        scaler = Scaler(
            np.asarray(info["mean"]),
            np.asarray(info["scale"]),
            np.asarray(info["floor"]),
        )
        pairs = [(k, t) for k in ORIGINS[c] for t in range(k, min(k + 180, c))]
        day_counts = pd.Series([t for _, t in pairs]).value_counts().to_dict()
        for strategy in ("IN", "OOF"):
            samples = pd.read_csv(out / f"samples_{n}_{strategy}.csv")
            close(samples["index"], [t for _, t in pairs])
            close(samples["origin"], [k for k, _ in pairs])
            close(samples["weight"], [1 / day_counts[t] for _, t in pairs])
            if list(samples.label_date) != [
                str((pd.Timestamp("2016-07-01") + pd.Timedelta(days=t)).date())
                for _, t in pairs
            ]:
                raise ValueError("Mean sample dates differ")
        base_n, features_n = teacher_arrays(
            source, n, teachers[str(n)]["recipe"], forcing[: n + 180], labels[0]
        )
        for strategy in ("P0", "IN", "OOF"):
            saved = np.load(out / f"prediction_{n}_{strategy}.npz")
            calibration = np.load(out / f"calibration_{n}_{strategy}.npz")
            if strategy == "P0":
                final, cal = base_n[None], base_c[None, c:n]
            else:
                final, cal = [], []
                for seed in range(3):
                    group = logs[
                        (logs.outer_days == n)
                        & (logs.strategy == strategy)
                        & (logs.seed == seed)
                    ]
                    if list(group.epoch) != list(range(1, 101)):
                        raise ValueError("Missing epoch or unexpected retry")
                    model = new_model(seed)
                    zero = torch.load(
                        out / f"mean_{n}_{strategy}_{seed}/e0.pt", weights_only=True
                    )
                    for key, value in model.state_dict().items():
                        if not torch.equal(value, zero[key]):
                            raise ValueError("Paired initial state differs")
                    model.load_state_dict(
                        torch.load(
                            out / f"mean_{n}_{strategy}_{seed}/e100.pt",
                            weights_only=True,
                        )
                    )
                    final.append(predict(model, base_n, features_n, scaler, chunk=17))
                    cal.append(
                        predict(model, base_c, features_c, scaler, chunk=17)[c:n]
                    )
                final, cal = np.stack(final), np.stack(cal)
            sigma = np.maximum(
                0.001, np.sqrt(np.mean((cal - labels[None, c:n]) ** 2, axis=1))
            )
            close(saved["means"], final)
            close(saved["scales"], sigma)
            close(calibration["means"], cal)
            close(calibration["scales"], sigma)
            dist = np.load(out / f"distribution_{n}_{strategy}.npz")
            valid_mean = final[:, 30:].mean(axis=0)
            close(dist["mean"], valid_mean)
            close(
                dist["std"],
                np.sqrt(
                    np.mean(
                        sigma[:, None] ** 2 + (final[:, 30:] - valid_mean) ** 2, axis=0
                    )
                ),
            )
            for level in (80, 90, 95):
                for side, probability in (
                    ("lower", (1 - level / 100) / 2),
                    ("upper", (1 + level / 100) / 2),
                ):
                    bound = dist[f"{side}_{level}"]
                    actual = np.mean(
                        norm.cdf((bound - final[:, 30:]) / sigma[:, None]), axis=0
                    )
                    close(
                        actual,
                        np.full_like(actual, probability),
                        atol=1e-6 / (float(sigma.min()) * np.sqrt(2 * np.pi)) + 1e-12,
                    )
            for part, start, end in (("train", 30, n), ("prediction", n, n + 180)):
                ids = slice(start - 30, end - 30)
                y = labels[start:end]
                m = final[:, start:end]
                s = np.broadcast_to(sigma[:, None], m.shape)
                error = m.mean(axis=0) - y
                scores = independent_crps(m, s, y)
                for j, station in enumerate(POINTS):
                    row = metrics[
                        (metrics.outer_days == n)
                        & (metrics.strategy == strategy)
                        & (metrics.part == part)
                        & (metrics.station == station)
                    ].iloc[0]
                    close(row.rmse_mm, np.sqrt(np.mean(error[:, j] ** 2)))
                    close(row.mae_mm, np.mean(abs(error[:, j])))
                    close(row.crps_mm, scores[:, j].mean())
                    close(row.days, end - start)
                    for level in (80, 90, 95):
                        lo, hi = (
                            dist[f"lower_{level}"][ids, j],
                            dist[f"upper_{level}"][ids, j],
                        )
                        actual = y[:, j]
                        close(
                            row[f"coverage_{level}"],
                            np.mean((lo <= actual) & (actual <= hi)),
                        )
                        close(row[f"width_{level}_mm"], np.mean(hi - lo))
                        score = (
                            hi
                            - lo
                            + 2
                            / (1 - level / 100)
                            * (np.maximum(lo - actual, 0) + np.maximum(actual - hi, 0))
                        )
                        close(row[f"interval_score_{level}_mm"], score.mean())
    comparisons = pd.read_csv(out / "mean_comparisons.csv")
    for row in comparisons.itertuples():
        passes = []
        for part in ("train", "prediction"):
            chosen = metrics[
                (metrics.outer_days == row.outer_days)
                & (metrics.strategy == row.strategy)
                & (metrics.station == row.station)
                & (metrics.part == part)
            ].iloc[0]
            for reference in ("P0", "IN"):
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
                        passes.append(difference < -1e-6)
        if bool(row.strict_mean_improvement) != all(passes):
            raise ValueError("Strict four-metric acceptance differs")
    return dict(
        passed=True,
        checkpoints_replayed=12,
        optimizer_log_rows=1200,
        metric_rows=48,
        numeric_values_checked=values_checked,
        max_absolute_recomputation_difference=max_difference,
        protected_files_checked=len(protected),
        source_files_checked=len(manifest["sources"]),
        new_optimizer_updates=0,
        physical_forward_calls=0,
        scope="Checkpoint/scale replay, prefix identities, lock order and independent probability/point arithmetic; no independent retraining or effectiveness claim.",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    print(verify(ROOT / "results/ootang_bplus_v1_5" / args.run_id))
