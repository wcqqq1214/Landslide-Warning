"""Independent origin/history reconstruction, checkpoint replay, and probabilities."""

from datetime import datetime

import numpy as np
import pandas as pd
from scipy.stats import norm
import torch

from physics_guided_forecast_error.artifacts import (
    ROOT,
    array_sha,
    check_hashes,
    check_index,
    load_observations,
    read_json,
    sha,
)
from physics_guided_sample_learning.core import FLOOR
from physics_guided_synchronized_correction.core import PREFIXES, CUTOFFS, read_teacher
from physics_guided_synchronized_correction.verify import independent_crps
from .core import POINTS, STRATEGIES, new_model
from .support import CONFIG_SHA, PLAN_SHA, source_guard, specification


def independent_windows(base, features, labels, pairs, physical, history, strategy):
    """Scalar-date reconstruction using the saved training-only normalization."""
    pm, ps = np.array(physical["mean"]), np.array(physical["scale"])
    hm, hs = np.array(history["mean"]), np.array(history["scale"])
    transformed = (features - pm) / ps
    output = []
    for origin, target in pairs:
        current = np.zeros((30, 23, 1, 4))
        for k in range(30):
            current[k, :20, 0] = transformed[target - 29 + k]
            i = origin - 30 + k
            residual = labels[i] - base[i]
            increment = residual - (labels[i - 1] - base[i - 1])
            if strategy == "H":
                current[k, 20:22, 0] = (np.array([residual, increment]) - hm) / hs
            current[k, 22, 0] = (target - origin) / 179
        output.append(current)
    return np.array(output)


def verify(out, sealed=True):
    spec = specification()
    if sealed:
        check_index(out)
        completed = read_json(out / "completed.json")
        if (
            not completed["execution_complete"]
            or not completed["numerical_verification"]
            or not 0 < completed["elapsed_seconds"] <= spec["hard_timeout_seconds"]
            or read_json(out / "launcher.json")["exitcode"] != 0
        ):
            raise ValueError("Original finite learning run was not successful")
    manifest = read_json(out / "manifest.json")
    if (
        manifest["specification"] != spec
        or manifest["config_sha256"] != CONFIG_SHA
        or manifest["plan_sha256"] != PLAN_SHA
    ):
        raise ValueError("Frozen learning specification differs")
    protected = source_guard(spec)
    if protected != read_json(out / "protected_before.json"):
        raise ValueError("Protected source inventory differs")
    check_hashes(manifest["sources"])
    check_hashes(manifest["sources"], out / "sources")
    if (
        not sha(out / "input.csv")
        == manifest["input_sha256"]
        == sha(ROOT / spec["input_csv"])
    ):
        raise ValueError("Input snapshot changed")
    physical, learning = ROOT / spec["physical_source"], ROOT / spec["learning_source"]
    previous_teachers = read_json(learning / "teachers.json")
    if read_json(out / "teachers.json") != {
        str(h): previous_teachers[str(h)] for h in PREFIXES
    }:
        raise ValueError("Frozen physical teachers changed")
    if read_json(out / "execution.json") != dict(
        neural_updates=1800,
        scale_fits=8,
        physical_forward_calls=0,
        physical_optimizer_nfev=0,
        scale_network_updates=0,
    ):
        raise ValueError("Execution budget differs")
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
        raise ValueError("Training log inventory differs")
    checkpoints = {
        f"model_{h}_{s}_{k}/e{e}.pt"
        for h in PREFIXES
        for s in ("C", "H")
        for k in range(3)
        for e in (0, 25, 50, 75, 100)
    }
    if {str(p.relative_to(out)) for p in out.glob("model_*/*.pt")} != checkpoints:
        raise ValueError("Checkpoint inventory differs")
    last_time = datetime.fromisoformat(manifest["started_utc"])
    previous = None
    for h in PREFIXES:
        lock = read_json(out / f"mean_lock_{h}.json")
        expected_files = {p for p in checkpoints if p.startswith(f"model_{h}_")}
        expected_files.update(f"mean_{h}_{s}.npz" for s in STRATEGIES)
        expected_files.update(
            f"{name}_{h}.{ext}"
            for name, ext in (
                ("scalers", "json"),
                ("constants", "json"),
                ("pairs", "csv"),
                ("queries", "csv"),
            )
        )
        if set(lock["files"]) != expected_files or lock["fit_days"] != h:
            raise ValueError("Incomplete prefix lock")
        check_hashes(lock["files"], out)
        constants = read_json(out / f"constants_{h}.json")
        if constants["prior_lock_sha256"] != previous:
            raise ValueError("Prefix lock chain differs")
        read_time, lock_time = (
            datetime.fromisoformat(constants["label_read_utc"]),
            datetime.fromisoformat(lock["locked_utc"]),
        )
        if not last_time <= read_time <= lock_time:
            raise ValueError("Later labels preceded the earlier forecast lock")
        last_time, previous = lock_time, sha(out / f"mean_lock_{h}.json")
    mean_lock = read_json(out / "mean_lock.json")
    if set(mean_lock["files"]) != {
        f"mean_lock_{h}.json" for h in PREFIXES
    } or last_time > datetime.fromisoformat(mean_lock["locked_utc"]):
        raise ValueError("Global mean lock differs")
    check_hashes(mean_lock["files"], out)
    prediction_lock = read_json(out / "prediction_lock.json")
    expected = {
        f"{name}_{n}_{s}.{ext}"
        for n in CUTOFFS
        for s in STRATEGIES
        for name, ext in (
            ("prediction", "npz"),
            ("calibration", "npz"),
            ("calibration", "json"),
        )
    }
    if set(prediction_lock["files"]) != expected:
        raise ValueError("Incomplete probability lock")
    check_hashes(prediction_lock["files"], out)
    scoring = read_json(out / "scoring.json")
    if scoring["prediction_lock_sha256"] != sha(
        out / "prediction_lock.json"
    ) or datetime.fromisoformat(scoring["label_read_utc"]) < datetime.fromisoformat(
        prediction_lock["locked_utc"]
    ):
        raise ValueError("Scoring preceded final prediction lock")
    _, _, labels = load_observations(out / "input.csv", 792)
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

    def verify_pair_table(path, pairs, h):
        frame = pd.read_csv(path)
        expected = pd.DataFrame(
            dict(
                origin=pairs[:, 0],
                target=pairs[:, 1],
                lead=pairs[:, 1] - pairs[:, 0],
                last_observation_index=pairs[:, 0] - 1,
                teacher_fit_days=h,
                part=np.where(pairs[:, 1] < h, "train", "prediction"),
            )
        )
        pd.testing.assert_frame_equal(frame, expected)

    forecasts = {}
    summaries = pd.read_csv(out / "model_summary.csv")
    if (
        len(summaries) != 18
        or summaries.duplicated(["fit_days", "strategy", "seed"]).any()
    ):
        raise ValueError("Model summary inventory differs")
    for h in PREFIXES:
        base, features = read_teacher(out / "input.csv", physical, h)
        constants, info = (
            read_json(out / f"constants_{h}.json"),
            read_json(out / f"scalers_{h}.json"),
        )
        if (
            constants["labels_sha256"] != array_sha(labels[:h])
            or constants["feature_sha256"] != array_sha(features)
            or constants["fit_days"] != h
            or info["fit_days"] != h
        ):
            raise ValueError("Training prefix identity differs")
        pm, ps = features[:h].mean(axis=0), np.maximum(features[:h].std(axis=0), FLOOR)
        pm[-4:], ps[-4:] = 0, 1
        close(info["physical"]["mean"], pm)
        close(info["physical"]["scale"], ps)
        close(info["physical"]["floor"], np.broadcast_to(FLOOR, pm.shape))
        e = labels[:h] - base[:h]
        history = np.array([[e[i], e[i] - e[i - 1]] for i in range(1, h)])
        hm, hs = (
            history.mean(axis=0),
            np.maximum(history.std(axis=0), np.array([1, 0.01])[:, None]),
        )
        close(info["history"]["mean"], hm)
        close(info["history"]["scale"], hs)
        close(
            info["history"]["floor"],
            np.broadcast_to(np.array([1, 0.01])[:, None], hm.shape),
        )
        with np.load(ROOT / spec["history_source"] / f"history_{h}.npz") as saved:
            close(saved["u"], labels[h - 30 : h], atol=1e-10)
            close(saved["du"], labels[h - 30 : h] - labels[h - 31 : h - 1], atol=1e-10)
            expected_dates = (
                pd.date_range("2016-07-01", periods=h)
                .strftime("%Y-%m-%d")
                .to_numpy()[-30:]
            )
            if not np.array_equal(saved["dates"], expected_dates):
                raise ValueError("Previously audited history dates differ")
        pairs = np.array(
            [
                (o, o + lead)
                for o in range(31, h, 14)
                for lead in spec["training_leads"]
                if o + lead < h
            ]
        )
        if len(pairs) != spec["training_pair_counts"][str(h)]:
            raise ValueError("Training query count differs")
        queries = np.array(
            [(o, t) for o in range(31, h, 180) for t in range(o, min(o + 180, h))]
            + [(h, t) for t in range(h, h + 180)]
        )
        verify_pair_table(out / f"pairs_{h}.csv", pairs, h)
        verify_pair_table(out / f"queries_{h}.csv", queries, h)
        for strategy in STRATEGIES:
            with np.load(out / f"mean_{h}_{strategy}.npz") as saved:
                means = saved["means"].copy()
            if strategy == "P0":
                close(means, base[None])
            elif strategy == "A":
                expected = np.full_like(base, np.nan)
                expected[29:31] = base[29:31]
                for o, t in queries:
                    expected[t] = base[t] + labels[o - 1] - base[o - 1]
                close(means, expected[None])
            else:
                train_x = independent_windows(
                    base,
                    features,
                    labels[:h],
                    pairs,
                    info["physical"],
                    info["history"],
                    strategy,
                )
                if array_sha(train_x) != constants["sample_hashes"][strategy]:
                    raise ValueError("Independent input windows differ")
                all_x = independent_windows(
                    base,
                    features,
                    labels[:h],
                    queries,
                    info["physical"],
                    info["history"],
                    strategy,
                )
                expected_means = []
                for seed in range(3):
                    group = logs[
                        (logs.fit_days == h)
                        & (logs.strategy == strategy)
                        & (logs.seed == seed)
                    ]
                    if group.epoch.tolist() != list(range(1, 101)):
                        raise ValueError("Missing update or unexpected retry")
                    model = new_model(seed)
                    directory = out / f"model_{h}_{strategy}_{seed}"
                    fresh = model.state_dict()
                    for epoch in (0, 25, 50, 75, 100):
                        state = torch.load(
                            directory / f"e{epoch}.pt", weights_only=True
                        )
                        if set(state) != set(fresh) or any(
                            state[k].shape != fresh[k].shape
                            or not torch.isfinite(state[k]).all()
                            for k in state
                        ):
                            raise ValueError("Invalid checkpoint tensor")
                        if epoch == 0 and any(
                            not torch.equal(state[k], fresh[k]) for k in state
                        ):
                            raise ValueError("Paired initialization differs")
                    with torch.no_grad():
                        close(
                            model(torch.as_tensor(train_x[:1])).numpy(),
                            np.zeros((1, 4)),
                        )
                        model.load_state_dict(state)
                        predicted = np.full_like(base, np.nan)
                        predicted[29:31] = base[29:31]
                        for start in range(0, len(queries), 53):
                            selected = queries[start : start + 53, 1]
                            predicted[selected] = (
                                base[selected]
                                + model(
                                    torch.as_tensor(all_x[start : start + 53])
                                ).numpy()
                            )
                        loss = float(
                            (
                                (
                                    torch.as_tensor(base[pairs[:, 1]])
                                    + model(torch.as_tensor(train_x))
                                    - torch.as_tensor(labels[pairs[:, 1]])
                                )
                                / 100
                            )
                            .square()
                            .mean()
                        )
                    expected_means.append(predicted)
                    row = summaries[
                        (summaries.fit_days == h)
                        & (summaries.strategy == strategy)
                        & (summaries.seed == seed)
                    ].iloc[0]
                    close(
                        [row.initial_loss, row.final_loss],
                        [
                            np.mean(
                                ((base[pairs[:, 1]] - labels[pairs[:, 1]]) / 100) ** 2
                            ),
                            loss,
                        ],
                    )
                close(means, np.stack(expected_means))
            if not np.isfinite(means[:, 30:]).all():
                raise ArithmeticError("Nonfinite scored prediction")
            forecasts[h, strategy] = means
    metrics = pd.read_csv(out / "metrics.csv")
    seed_metrics = pd.read_csv(out / "seed_metrics.csv")
    for frame, count, keys in (
        (metrics, 64, ["outer_days", "strategy", "station", "part"]),
        (seed_metrics, 128, ["outer_days", "strategy", "station", "part", "component"]),
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
        for strategy in STRATEGIES:
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
                        component = (
                            "deterministic" if strategy in ("P0", "A") else f"seed_{k}"
                        )
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
        len(comparisons) != 24
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
            for reference in ("P0", "A", "C"):
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
    result = dict(
        passed=True,
        checkpoint_replays=18,
        saved_checkpoints=90,
        training_updates=1800,
        metrics_rows=64,
        seed_metrics_rows=128,
        comparison_rows=24,
        numeric_values_checked=values_checked,
        max_absolute_difference=max_difference,
        protected_files_checked=len(protected),
        source_files_checked=len(manifest["sources"]),
        new_physical_forwards=0,
        new_neural_updates=0,
        raw_observation_as_of_verified="unknown",
    )
    if sealed and result != read_json(out / "verification.json"):
        raise ValueError("Sealed numerical report differs")
    return result
