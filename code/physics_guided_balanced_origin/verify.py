"""Independent checkpoint balancing and the retained v1.19 probability verification."""

from datetime import datetime
from collections import Counter

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
from physics_guided_sample_learning.core import FLOOR, POINTS, CUTOFFS
from .support import read_teachers

from physics_guided_synchronized_correction.verify import independent_crps
from .core import STRATEGIES
from .audit import checkpoint as audit_checkpoint, verify_logs
from physics_guided_origin_gradients.core import Budget
from physics_guided_origin_learning.core import Samples
from physics_guided_history_learning.core import new_model
from .support import CONFIG_SHA, PLAN_SHA, source_guard, specification

PREFIXES = (342, 432, 612)


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


def independent_samples(h, strategy, labels, teachers, scalers):
    if strategy not in ("IN", "OOF") or labels.shape != (h, 4):
        raise ValueError("Independent samples require the exact training prefix")
    anchors = [
        (o, o + step, 0)
        for o in range(31, h, 14)
        for step in (0, 6, 29, 59, 89, 119, 149, 179)
        if o + step < h
    ]
    paired = [
        (o, t, 1) for o in (252, 342, 432) if o < h for t in range(o, min(o + 180, h))
    ]
    multiplicity = Counter(t for _, t, _ in paired)
    rows = []
    for o, t, block in anchors + paired:
        rows.append(
            dict(
                origin=o,
                target=t,
                lead=t - o,
                block=block,
                teacher_IN=h,
                teacher_OOF=h if block == 0 else o,
                last_observation_index=o - 1,
                weight=0.5 / len(anchors)
                if block == 0
                else 0.5 / (len(multiplicity) * multiplicity[t]),
            )
        )
    table = pd.DataFrame(rows)
    x = np.empty((len(rows), 30, 23, 1, 4))
    bases = np.empty((len(rows), 4))
    for teacher in sorted(set(int(r[f"teacher_{strategy}"]) for r in rows)):
        positions = np.array(
            [i for i, r in enumerate(rows) if r[f"teacher_{strategy}"] == teacher]
        )
        pairs = np.array([[rows[i]["origin"], rows[i]["target"]] for i in positions])
        base, features = teachers[teacher]
        bound = h if teacher == h else teacher
        x[positions] = independent_windows(
            base,
            features,
            labels[:bound],
            pairs,
            scalers["physical"],
            scalers["history"],
            "H",
        )
        bases[positions] = base[pairs[:, 1]]
    payload = dict(
        base=bases,
        target=np.array([labels[r["target"]] for r in rows]),
        weights=np.array([r["weight"] for r in rows]),
        blocks=np.array([r["block"] for r in rows]),
    )
    return table, x, payload


def independent_losses(payload, correction):
    residual = (payload["base"] + correction - payload["target"]) / 100
    losses = {}
    for block, key in ((0, "anchor_loss"), (1, "paired_loss")):
        positions = np.flatnonzero(payload["blocks"] == block)
        losses[key] = (
            sum(
                float(payload["weights"][i])
                * sum(float(v) ** 2 for v in residual[i])
                / 4
                for i in positions
            )
            / 0.5
        )
    return dict(loss=0.5 * (losses["anchor_loss"] + losses["paired_loss"]), **losses)


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
    physical, learning = (
        ROOT / spec["physical_source"],
        ROOT / spec["history_learning_source"],
    )
    previous_teachers = read_json(ROOT / spec["teacher_source"] / "teachers.json")
    if read_json(out / "teachers.json") != previous_teachers:
        raise ValueError("Frozen physical teachers changed")
    if read_json(out / "execution.json") != dict(
        neural_updates=1800,
        scale_fits=6,
        prediction_forward_calls=90,
        gradient_calls=9696,
        gradient_backward_calls=9696,
        physical_forward_calls=0,
        physical_optimizer_nfev=0,
        scale_network_updates=0,
        scaler_fits=0,
    ):
        raise ValueError("Execution budget differs")
    logs = pd.read_csv(out / "training.csv")
    if (
        len(logs) != 1800
        or logs.duplicated(["fit_days", "strategy", "seed", "epoch"]).any()
        or not np.isfinite(logs.select_dtypes(include="number")).all().all()
    ):
        raise ValueError("Training log inventory differs")
    checkpoints = {
        f"model_{h}_{s}_{k}/e{e}.pt"
        for h in PREFIXES
        for s in ("IN", "OOF")
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
        expected_files.update(f"samples_{h}_{s}.npz" for s in ("IN", "OOF"))
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
    gradient_budget = Budget(960, 0)
    replay_forward_calls = 0

    def count_forward(*_):
        nonlocal replay_forward_calls
        replay_forward_calls += 1

    def close(actual, expected, atol=1e-8, rtol=0):
        nonlocal values_checked, max_difference
        a, b = np.asarray(actual), np.asarray(expected)
        if a.shape != b.shape or not np.allclose(
            a, b, rtol=rtol, atol=atol, equal_nan=True
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
    if sha(out / "reference_metrics.csv") != sha(
        ROOT / spec["equal_source"] / "metrics.csv"
    ):
        raise ValueError("Frozen equal-weight reference metrics differ")
    verify_logs(logs, summaries, pd.read_csv(out / "optimizer_diagnostics.csv"))
    for h in PREFIXES:
        pool = read_teachers(out / "input.csv", physical, h)
        base, features = pool[h]
        constants = read_json(out / f"constants_{h}.json")
        info = read_json(out / f"scalers_{h}.json")
        if (
            constants["labels_sha256"] != array_sha(labels[:h])
            or constants["feature_sha256"] != array_sha(features)
            or constants["fit_days"] != h
            or info["fit_days"] != h
            or sha(out / f"scalers_{h}.json") != sha(learning / f"scalers_{h}.json")
        ):
            raise ValueError("Frozen prefix or shared scaler differs")
        pm, ps = features[:h].mean(axis=0), np.maximum(features[:h].std(axis=0), FLOOR)
        pm[-4:], ps[-4:] = 0, 1
        close(info["physical"]["mean"], pm)
        close(info["physical"]["scale"], ps)
        e = labels[:h] - base[:h]
        history = np.array([[e[i], e[i] - e[i - 1]] for i in range(1, h)])
        close(info["history"]["mean"], history.mean(axis=0))
        close(
            info["history"]["scale"],
            np.maximum(history.std(axis=0), np.array([1, 0.01])[:, None]),
        )
        queries = np.array(
            [(o, t) for o in range(31, h, 180) for t in range(o, min(o + 180, h))]
            + [(h, t) for t in range(h, h + 180)]
        )
        verify_pair_table(out / f"queries_{h}.csv", queries, h)
        evaluation_x = independent_windows(
            base, features, labels[:h], queries, info["physical"], info["history"], "H"
        )
        reference_anchor = None
        for strategy in STRATEGIES:
            with np.load(out / f"mean_{h}_{strategy}.npz", allow_pickle=False) as saved:
                means = saved["means"].copy()
            if strategy == "P0":
                close(means, base[None])
                forecasts[h, strategy] = means
                continue
            table, train_x, payload = independent_samples(
                h, strategy, labels[:h], pool, info
            )
            pd.testing.assert_frame_equal(
                pd.read_csv(out / f"pairs_{h}.csv"),
                table,
                check_exact=False,
                rtol=0,
                atol=1e-15,
            )
            if (
                len(table) != spec["total_counts"][str(h)]
                or int((table.block == 0).sum()) != spec["anchor_counts"][str(h)]
                or int((table.block == 1).sum()) != spec["paired_counts"][str(h)]
                or table[table.block == 1].target.nunique()
                != spec["unique_paired_dates"][str(h)]
            ):
                raise ValueError("Registered sample inventory differs")
            equal_constants = read_json(
                ROOT / spec["equal_source"] / f"constants_{h}.json"
            )
            if not (
                array_sha(train_x)
                == constants["sample_hashes"][strategy]
                == equal_constants["sample_hashes"][strategy]
            ):
                raise ValueError("Independent paired inputs differ")
            with np.load(
                out / f"samples_{h}_{strategy}.npz", allow_pickle=False
            ) as saved:
                if set(saved.files) != set(payload):
                    raise ValueError("Sample payload inventory differs")
                for name in payload:
                    close(saved[name], payload[name])
            anchor = payload["blocks"] == 0
            if reference_anchor is None:
                reference_anchor = (
                    train_x[anchor].copy(),
                    payload["base"][anchor].copy(),
                    payload["target"].copy(),
                    payload["weights"].copy(),
                )
            else:
                for actual, expected in zip(
                    (
                        train_x[anchor],
                        payload["base"][anchor],
                        payload["target"],
                        payload["weights"],
                    ),
                    reference_anchor,
                ):
                    np.testing.assert_array_equal(actual, expected)
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
                handle = model.register_forward_hook(count_forward)
                directory = out / f"model_{h}_{strategy}_{seed}"
                fresh = model.state_dict()
                for epoch in (0, 25, 50, 75, 100):
                    state = torch.load(directory / f"e{epoch}.pt", weights_only=True)
                    if set(state) != set(fresh) or any(
                        state[k].shape != fresh[k].shape
                        or state[k].dtype != fresh[k].dtype
                        or not torch.isfinite(state[k]).all()
                        for k in state
                    ):
                        raise ValueError("Invalid checkpoint tensor")
                    if epoch == 0 and any(
                        not torch.equal(state[k], fresh[k]) for k in state
                    ):
                        raise ValueError("Paired initialization differs")
                    if epoch == 0:
                        equal_state = torch.load(
                            ROOT
                            / spec["equal_source"]
                            / f"model_{h}_{strategy}_{seed}/e0.pt",
                            weights_only=True,
                        )
                        if set(equal_state) != set(state) or any(
                            not torch.equal(equal_state[k], state[k]) for k in state
                        ):
                            raise ValueError(
                                "NORM initialization differs from frozen EQ"
                            )
                    model.load_state_dict(state)
                    sample = Samples(
                        torch.as_tensor(train_x),
                        torch.as_tensor(payload["base"]),
                        torch.as_tensor(payload["target"]),
                        torch.as_tensor(payload["weights"]),
                        payload["blocks"],
                    )
                    expected_profile = audit_checkpoint(model, sample, gradient_budget)
                    if epoch < 100:
                        logged = group[group.epoch == epoch + 1].iloc[0]
                        for name, value in expected_profile.items():
                            key = "loss_before_update" if name == "loss" else name
                            close(
                                logged[key],
                                value,
                                atol=1e-10 if "loss" in name else 1e-9,
                                rtol=1e-10 if "loss" in name else 1e-9,
                            )
                    else:
                        logged = summaries[
                            (summaries.fit_days == h)
                            & (summaries.strategy == strategy)
                            & (summaries.seed == seed)
                        ].iloc[0]
                        for name, value in expected_profile.items():
                            close(
                                logged["final_" + name],
                                value,
                                atol=1e-10 if "loss" in name else 1e-9,
                                rtol=1e-10 if "loss" in name else 1e-9,
                            )
                model.load_state_dict(
                    torch.load(directory / "e0.pt", weights_only=True)
                )
                with torch.no_grad():
                    close(model(torch.as_tensor(train_x[:1])).numpy(), np.zeros((1, 4)))
                    model.load_state_dict(state)
                    predicted = np.full_like(base, np.nan)
                    predicted[29:31] = base[29:31]
                    for start in range(0, len(queries), 53):
                        targets = queries[start : start + 53, 1]
                        predicted[targets] = (
                            base[targets]
                            + model(
                                torch.as_tensor(evaluation_x[start : start + 53])
                            ).numpy()
                        )
                    train_output = np.concatenate(
                        [
                            model(torch.as_tensor(train_x[start : start + 53])).numpy()
                            for start in range(0, len(train_x), 53)
                        ]
                    )
                handle.remove()
                expected_means.append(predicted)
                initial = independent_losses(payload, np.zeros_like(payload["base"]))
                final = independent_losses(payload, train_output)
                rows = summaries[
                    (summaries.fit_days == h)
                    & (summaries.strategy == strategy)
                    & (summaries.seed == seed)
                ]
                if len(rows) != 1:
                    raise ValueError("Training summary row missing")
                row = rows.iloc[0]
                for phase, losses in (("initial", initial), ("final", final)):
                    for key, value in losses.items():
                        close(row[f"{phase}_{key}"], value, atol=1e-10, rtol=1e-10)
                close(
                    group.iloc[0].loss_before_update,
                    initial["loss"],
                    atol=1e-10,
                    rtol=1e-10,
                )
            close(means, np.stack(expected_means))
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
                        checks.append(difference)
        if bool(row.strict_mean_improvement) != all(x < -1e-6 for x in checks):
            raise ArithmeticError("Strict acceptance differs")
    reference = pd.read_csv(out / "reference_metrics.csv").set_index(
        ["outer_days", "strategy", "station", "part"]
    )
    weighting = pd.read_csv(out / "weighting_comparisons.csv")
    if (
        len(weighting) != 16
        or weighting.duplicated(["outer_days", "strategy", "station"]).any()
    ):
        raise ValueError("Weighting comparison inventory differs")
    for row in weighting.itertuples():
        strict = []
        for part in ("train", "prediction"):
            chosen = metrics[
                (metrics.outer_days == row.outer_days)
                & (metrics.strategy == row.strategy)
                & (metrics.station == row.station)
                & (metrics.part == part)
            ].iloc[0]
            previous = reference.loc[(row.outer_days, row.strategy, row.station, part)]
            for metric in ("rmse_mm", "mae_mm", "crps_mm"):
                change = chosen[metric] - previous[metric]
                close(getattr(row, f"{part}_{metric}_minus_EQ"), change)
                if metric != "crps_mm":
                    strict.append(change)
        if bool(row.strict_mean_improvement_over_EQ) != all(x < -1e-6 for x in strict):
            raise ArithmeticError("EQ comparison acceptance differs")
    if (
        gradient_budget.gradient_calls != 960
        or gradient_budget.backward_calls != 960
        or replay_forward_calls != 1374
    ):
        raise ValueError("Independent checkpoint call count differs")
    result = dict(
        gradient_checkpoint_replays=90,
        replay_gradient_calls=960,
        replay_forward_calls=replay_forward_calls,
        weighting_comparison_rows=16,
        optimizer_diagnostic_rows=1800,
        passed=True,
        checkpoint_replays=18,
        saved_checkpoints=90,
        training_updates=1800,
        metrics_rows=48,
        seed_metrics_rows=112,
        comparison_rows=16,
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
