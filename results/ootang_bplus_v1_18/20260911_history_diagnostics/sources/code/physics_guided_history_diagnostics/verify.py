"""Independent scalar summaries, date reconstruction, and checkpoint replay."""

from datetime import datetime
from math import fsum, sqrt

import numpy as np
import pandas as pd
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
from physics_guided_history_learning.verify import independent_windows
from physics_guided_synchronized_correction.core import read_teacher
from .core import evaluate
from .workflow import CONFIG_SHA, PLAN_SHA, frozen_model, source_guard, specification


def avg(values):
    return fsum(values) / len(values)


def scalar_demand(need, training):
    lo, hi = min(training), max(training)
    excess = [max(lo - x, x - hi, 0.0) for x in need]
    rms, trms = sqrt(avg([x * x for x in need])), sqrt(avg([x * x for x in training]))
    return dict(
        count=len(need),
        need_mean_mm=avg(need),
        need_rms_mm=rms,
        need_min_mm=min(need),
        need_max_mm=max(need),
        training_count=len(training),
        training_need_min_mm=lo,
        training_need_max_mm=hi,
        training_need_rms_mm=trms,
        outside_training_count=sum(x > 0 for x in excess),
        outside_training_fraction=sum(x > 0 for x in excess) / len(need),
        maximum_excess_mm=max(excess),
        need_rms_over_training=rms / trms if trms > 1e-8 else np.nan,
    )


def scalar_models(need, full, zeroed):
    result = dict(count=len(need))
    for name, d in (("H", full), ("H0", zeroed)):
        err = [x - y for x, y in zip(d, need)]
        result.update(
            {
                name + "_correction_mean_mm": avg(d),
                name + "_correction_rms_mm": sqrt(avg([x * x for x in d])),
                name + "_rmse_mm": sqrt(avg([x * x for x in err])),
                name + "_mae_mm": avg([abs(x) for x in err]),
                name + "_bias_mm": avg(err),
                name + "_active_count": sum(abs(x) > 1e-6 for x in d),
                name + "_wrong_direction_count": sum(
                    abs(x) > 1e-6 and abs(y) > 1e-6 and ((x > 0) != (y > 0))
                    for x, y in zip(d, need)
                ),
                name + "_mse_minus_P0_mm2": avg([x * x for x in err])
                - avg([x * x for x in need]),
                name + "_mse_identity_mm2": avg(
                    [x * x - 2 * x * y for x, y in zip(d, need)]
                ),
            }
        )
    effect = [x - y for x, y in zip(full, zeroed)]
    result.update(
        history_effect_mean_mm=avg(effect),
        history_effect_rms_mm=sqrt(avg([x * x for x in effect])),
        history_effect_max_abs_mm=max(abs(x) for x in effect),
        H_minus_H0_rmse_mm=result["H_rmse_mm"] - result["H0_rmse_mm"],
        H_minus_H0_mae_mm=result["H_mae_mm"] - result["H0_mae_mm"],
        H_minus_H0_mse_mm2=avg(
            [(a - r) ** 2 - (b - r) ** 2 for r, a, b in zip(need, full, zeroed)]
        ),
        history_mse_identity_mm2=avg(
            [s * s + 2 * s * (b - r) for r, b, s in zip(need, zeroed, effect)]
        ),
    )
    return result


def verify(out, sealed=True):
    spec = specification()
    if sealed:
        check_index(out)
        completed = read_json(out / "completed.json")
        if (
            not completed["execution_complete"]
            or not completed["numerical_verification"]
            or not 0 < completed["elapsed_seconds"] <= spec["hard_timeout_seconds"]
            or completed["scientific_forward_calls"] != 408
            or read_json(out / "launcher.json")["exitcode"] != 0
        ):
            raise ValueError("Original diagnostic execution was not successful")
    manifest = read_json(out / "manifest.json")
    if (
        manifest["specification"] != spec
        or manifest["config_sha256"] != CONFIG_SHA
        or manifest["plan_sha256"] != PLAN_SHA
    ):
        raise ValueError("Frozen diagnostic specification differs")
    protected = source_guard(spec)
    if read_json(out / "protected_before.json") != protected:
        raise ValueError("Protected inventory differs")
    check_hashes(manifest["sources"])
    check_hashes(manifest["sources"], out / "sources")
    expected_execution = {
        k: spec[k]
        for k in (
            "primary_forward_calls",
            "neural_updates",
            "gradient_calls",
            "physical_forward_calls",
            "physical_optimizer_nfev",
            "scaler_fits",
            "probability_scale_fits",
        )
    }
    if read_json(out / "execution.json") != expected_execution:
        raise ValueError("Execution budget differs")
    lock, scoring = read_json(out / "output_lock.json"), read_json(out / "scoring.json")
    expected_files = {
        f"{name}_{h}.{ext}"
        for h in spec["prefixes"]
        for name, ext in (("prefix", "npz"), ("inputs", "json"))
    }
    if (
        set(lock["files"]) != expected_files
        or scoring["label_rows"] != 792
        or scoring["output_lock_sha256"] != sha(out / "output_lock.json")
    ):
        raise ValueError("Prediction/scoring lock differs")
    check_hashes(lock["files"], out)
    stamp = datetime.fromisoformat(manifest["started_utc"])
    for h in spec["prefixes"]:
        info = read_json(out / f"inputs_{h}.json")
        read, done = (
            datetime.fromisoformat(info["label_read_utc"]),
            datetime.fromisoformat(info["output_locked_utc"]),
        )
        if (
            not stamp <= read <= done
            or info["output_sha256"] != sha(out / f"prefix_{h}.npz")
            or info["fit_days"] != h
        ):
            raise ValueError("Prefix ordering or output identity differs")
        stamp = done
    if (
        not stamp
        <= datetime.fromisoformat(lock["locked_utc"])
        <= datetime.fromisoformat(scoring["label_read_utc"])
    ):
        raise ValueError("Scoring preceded output locks")
    source = ROOT / spec["source_run"]
    old_spec = read_json(source / "manifest.json")["specification"]
    _, _, labels = load_observations(source / "input.csv", 792)
    if array_sha(labels) != scoring["labels_sha256"]:
        raise ValueError("Scoring label identity differs")
    checked, maximum, input_checked = 0, 0.0, 0

    def close(a, b):
        nonlocal checked, maximum
        a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
        if a.shape != b.shape or not np.allclose(
            a, b, atol=1e-8, rtol=0, equal_nan=True
        ):
            raise ArithmeticError(
                f"Independent numerical comparison differs: {a.shape}, {b.shape}"
            )
        finite = np.isfinite(a) & np.isfinite(b)
        if finite.any():
            maximum = max(maximum, float(abs(a[finite] - b[finite]).max()))
        checked += a.size

    tables = {
        name: pd.read_csv(out / f"{name}.csv")
        for name in ("demand", "models", "lead_coverage")
    }
    keys = ["fit_days", "part", "band", "station"]
    for name, count in (("demand", 144), ("models", 576), ("lead_coverage", 540)):
        key = (
            ["fit_days", "lead"]
            if name == "lead_coverage"
            else keys + (["seed"] if name == "models" else [])
        )
        if len(tables[name]) != count or tables[name].duplicated(key).any():
            raise ValueError("Summary table inventory differs")
    demand_index = tables["demand"].set_index(keys)
    model_index = tables["models"].set_index(keys + ["seed"])
    coverage_index = tables["lead_coverage"].set_index(["fit_days", "lead"])
    old_metrics = pd.read_csv(source / "metrics.csv")
    old_seeds = pd.read_csv(source / "seed_metrics.csv")
    old_losses = pd.read_csv(source / "model_summary.csv")
    calls = {"forward_calls": 0}
    for h in spec["prefixes"]:
        base, features = read_teacher(
            source / "input.csv", ROOT / old_spec["physical_source"], h
        )
        scalers = read_json(source / f"scalers_{h}.json")
        info = read_json(out / f"inputs_{h}.json")
        if info["labels_sha256"] != array_sha(labels[:h]):
            raise ValueError("Inference labels extend beyond their frozen prefix")
        pairs = np.array(
            [
                (o, o + step)
                for o in range(31, h, 14)
                for step in (0, 6, 29, 59, 89, 119, 149, 179)
                if o + step < h
            ]
        )
        count_train = len(pairs)
        evaluation = np.array(
            [(o, t) for o in range(31, h, 180) for t in range(o, min(o + 180, h))]
            + [(h, t) for t in range(h, h + 180)]
        )
        all_pairs = np.concatenate([pairs, evaluation])
        kind = np.array([0] * count_train + [1] * (h - 31) + [2] * 180)
        if len(all_pairs) != spec["query_counts"][str(h)]:
            raise ValueError("Query budget differs")
        for name, expected in (("pairs", pairs), ("queries", evaluation)):
            np.testing.assert_array_equal(
                pd.read_csv(source / f"{name}_{h}.csv")[
                    ["origin", "target"]
                ].to_numpy(),
                expected,
            )
        with np.load(out / f"prefix_{h}.npz", allow_pickle=False) as z:
            saved = {k: z[k] for k in z.files}
        if set(saved) != {"pairs", "kind", "base", "H", "H0"}:
            raise ValueError("Unexpected diagnostic array inventory")
        np.testing.assert_array_equal(saved["pairs"], all_pairs)
        np.testing.assert_array_equal(saved["kind"], kind)
        close(saved["base"], base)
        for variant, mode in (("H", "H"), ("H0", "C")):
            # C here denotes the independent constructor's input mask, never C weights.
            x = independent_windows(
                base,
                features,
                labels[:h],
                all_pairs,
                scalers["physical"],
                scalers["history"],
                mode,
            )
            if array_sha(x) != info[variant + "_input_sha256"]:
                raise ValueError("Independent standardized inputs differ")
            input_checked += x.size
            if saved[variant].shape != (3, len(all_pairs), 4):
                raise ValueError("Unexpected correction shape")
            for seed in spec["seeds"]:
                model = frozen_model(source, h, seed)
                replay = evaluate(
                    model,
                    torch.as_tensor(x),
                    spec["replay_batch"],
                    calls,
                    spec["replay_forward_calls"],
                )
                close(saved[variant][seed], replay)
        with np.load(source / f"mean_{h}_H.npz") as z:
            close(
                base[evaluation[:, 1]][None] + saved["H"][:, count_train:],
                z["means"][:, evaluation[:, 1]],
            )
            close(z["means"][:, 30], np.repeat(base[None, 30], 3, axis=0))
        need = np.concatenate(
            [
                labels[all_pairs[:, 1]] - base[all_pairs[:, 1]],
                (labels[30] - base[30])[None],
            ]
        )
        kind = np.r_[kind, 1]
        lead = np.r_[all_pairs[:, 1] - all_pairs[:, 0], -1]
        values = {
            k: np.concatenate([saved[k], np.zeros((3, 1, 4))], axis=1)
            for k in ("H", "H0")
        }
        for seed in spec["seeds"]:
            loss = avg(
                [
                    (float(saved["H"][seed, i, j]) - float(need[i, j])) ** 2 / 10000
                    for i in range(count_train)
                    for j in range(4)
                ]
            )
            old = old_losses[
                (old_losses.fit_days == h)
                & (old_losses.strategy == "H")
                & (old_losses.seed == seed)
            ]
            if len(old) != 1:
                raise ValueError("Original loss row missing")
            close(loss, old.final_loss.item())
        for step in range(180):
            close(
                coverage_index.loc[(h, step)].to_numpy(),
                [sum((kind == i) & (lead == step)) for i in range(3)],
            )
        for i, part in enumerate(("training_pairs", "fit_readout", "prediction")):
            for band, lower, upper in (
                ("all", -1, 180),
                ("1_30", 0, 30),
                ("31_90", 30, 90),
                ("91_180", 90, 180),
            ):
                ix = [
                    k
                    for k in range(len(kind))
                    if kind[k] == i and lower <= lead[k] < upper
                ]
                ti = [
                    k
                    for k in range(len(kind))
                    if kind[k] == 0 and lower <= lead[k] < upper
                ]
                for j, station in enumerate(spec["point_order"]):
                    key = (h, part, band, station)
                    required = [float(need[k, j]) for k in ix]
                    expected = scalar_demand(required, [float(need[k, j]) for k in ti])
                    row = demand_index.loc[key]
                    if set(row.index) != set(expected):
                        raise ValueError("Demand statistic fields differ")
                    close(row.to_numpy(), [expected[k] for k in row.index])
                    for seed in ("0", "1", "2", "ensemble"):
                        d = {
                            v: [
                                fsum(float(values[v][s, k, j]) for s in range(3)) / 3
                                if seed == "ensemble"
                                else float(values[v][int(seed), k, j])
                                for k in ix
                            ]
                            for v in ("H", "H0")
                        }
                        expected = scalar_models(required, d["H"], d["H0"])
                        row = model_index.loc[key + (seed,)]
                        if set(row.index) != set(expected):
                            raise ValueError("Model statistic fields differ")
                        close(row.to_numpy(), [expected[k] for k in row.index])
                        for v in ("H", "H0"):
                            close(
                                row[v + "_mse_minus_P0_mm2"],
                                row[v + "_mse_identity_mm2"],
                            )
                        close(
                            row["H_minus_H0_mse_mm2"], row["history_mse_identity_mm2"]
                        )
                        if (
                            h in (432, 612)
                            and band == "all"
                            and part in ("fit_readout", "prediction")
                        ):
                            part_old = (
                                "train" if part == "fit_readout" else "prediction"
                            )
                            old = (
                                old_metrics
                                if seed == "ensemble"
                                else old_seeds[old_seeds.component == f"seed_{seed}"]
                            )
                            old = old[
                                (old.outer_days == h)
                                & (old.strategy == "H")
                                & (old.part == part_old)
                                & (old.station == station)
                            ]
                            if len(old) != 1:
                                raise ValueError("Original H metric missing")
                            close(
                                [row.H_rmse_mm, row.H_mae_mm],
                                old[["rmse_mm", "mae_mm"]].to_numpy()[0],
                            )
    if calls["forward_calls"] != spec["replay_forward_calls"]:
        raise ValueError("Replay budget differs")
    check_hashes(protected)
    return dict(
        passed=True,
        checkpoint_replays=18,
        replay_forward_calls=calls["forward_calls"],
        input_values_checked=input_checked,
        numeric_values_checked=checked,
        max_absolute_difference=maximum,
        demand_rows=144,
        model_rows=576,
        lead_coverage_rows=540,
        protected_files_checked=len(protected),
        new_neural_updates=0,
        new_physical_forwards=0,
        raw_observation_as_of_verified="unknown",
    )
