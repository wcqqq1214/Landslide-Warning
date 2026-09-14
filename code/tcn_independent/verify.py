"""Read-only reproduction, manual saved-history replay, and scalar score audit."""

import argparse
import json
import math
import time

import numpy as np
import pandas as pd
import torch

from rolling_probability.data import Teacher, example
from short_horizon.common import ROOT, CALLS, array_sha, save_json, sha, now
from short_horizon.data import observations
from short_horizon.physics import PhysicalBank
from short_horizon.verify import close
from .engine import read_spec, Learners, forecast_origin
from .run import arrays, check_lock


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    spec = read_spec(args.config)
    root = ROOT / spec["output_root"]
    out = root / "verification/receipt.json"
    if out.exists():
        raise FileExistsError(out)
    torch.set_num_threads(1)
    start_time = time.monotonic()
    for phase in ["calibration", "forecast", "score"]:
        check_lock(root / phase)
    for p, digest in json.loads(
        (root / "implementation_lock.json").read_text()
    ).items():
        assert sha(ROOT / p) == digest, p
    forecast_events = [
        json.loads(x) for x in (root / "forecast/events.jsonl").read_text().splitlines()
    ]
    score_events = [
        json.loads(x) for x in (root / "score/events.jsonl").read_text().splitlines()
    ]
    locked = next(
        e for e in forecast_events if e["event"] == "independent_distribution_locked"
    )
    released = next(
        e
        for e in score_events
        if e["event"] == "final_labels_released_after_distribution_lock"
    )
    assert locked["time_utc"] < released["time_utc"]
    assert locked["means_sha256"] == sha(root / "forecast/means.npz")
    assert locked["sigma_sha256"] == sha(root / "forecast/sigma.npz")
    y, f, _ = observations(spec, 1168)
    learners = Learners(spec)
    bank = PhysicalBank(f, y[0], [612, 792, 1168])
    cal = arrays(root / "calibration/forecasts.npz")
    cal_seeds = arrays(root / "calibration/seed_forecasts.npz")
    scales = arrays(root / "calibration/scales.npz")
    max_model = max_scale = 0.0
    for i, (n, length) in enumerate(zip(cal["origins"], cal["lengths"])):
        means, seeds, audit = forecast_origin(
            spec, y, bank, learners, int(n), int(length)
        )
        assert audit["model_prefix"] <= n and audit["physical_prefix"] <= n
        assert audit["future_forcing_constant"]
        for name in spec["models"]:
            max_model = max(max_model, close(means[name], cal[name][i, :length], 1e-9))
        for name in cal_seeds:
            max_model = max(
                max_model, close(seeds[name], cal_seeds[name][i, :, :length], 1e-9)
            )
        if i % 60 == 0:
            print(f"Independent reproduction: {i + 1}/382", flush=True)
    for k in range(293):
        pool = np.arange(1078 - k, 1168 - k)
        assert np.array_equal(pool, scales["pool_origins"][k])
        assert np.all(pool + k < 1168)
        for name in spec["models"]:
            errs = np.stack([y[n + k] - cal[name][n - 786, k] for n in pool])
            value = np.maximum(np.sqrt(np.einsum("np,np->p", errs, errs) / 90), 1e-6)
            max_scale = max(max_scale, close(value, scales[name][k], 1e-9))
    saved = arrays(root / "forecast/means.npz")
    seed_means = arrays(root / "forecast/seed_means.npz")
    sd = arrays(root / "forecast/sigma.npz")
    audit = json.loads((root / "forecast/origin_audit.json").read_text())
    q, forcing, b, state, _, _ = bank.forecast(1168, 294)
    teacher = Teacher(
        q, b, forcing, state["moisture"], state["rain_head"], state["reservoir_head"]
    )
    max_model = max(
        max_model, close(saved["B_ANCHOR"], y[-1] + b[1168:1461] - b[1167], 1e-9)
    )
    max_model = max(
        max_model,
        close(
            saved["DRIFT1"], y[-1] + np.arange(1, 294)[:, None] * (y[-1] - y[-2]), 1e-9
        ),
    )
    blocks = 0
    for name in ["RR_DIRECT", "TCN_DIRECT", "TCN_BRES"]:
        for seed, mu in enumerate(seed_means[name]):
            for i, offset in enumerate(range(0, 293, 7)):
                history = np.concatenate([y, mu[:offset]])
                trace = audit["traces"][name][seed][i]
                assert trace["observed_prefix"] == 1168
                assert trace["history_sha256"] == array_sha(history)
                x, z, baselines = example(history, teacher, horizon=7, window=30)
                data = dict(
                    x=x[None],
                    z=z[None],
                    last_y=history[-1][None],
                    anchor=baselines["B_ANCHOR"][None],
                )
                block = learners.block(1168, name, seed, data)
                count = min(7, 293 - offset)
                max_model = max(
                    max_model, close(block[:count], mu[offset : offset + count], 1e-9)
                )
                assert trace["output_sha256"] == array_sha(block)
                blocks += 1
        max_model = max(max_model, close(seed_means[name].mean(0), saved[name], 1e-9))
    for name in spec["models"]:
        max_scale = max(max_scale, close(sd[name], scales[name], 0))
    # Label loading here is only for saved-output scoring, after immutable locks.
    observed, _, dates = observations(spec, 1461)
    metric = pd.read_csv(root / "score/metrics_by_point.csv")
    source = pd.read_csv(root / "score/forecasts.csv")
    maximum = 0.0
    cells = 0
    quantiles = {80: 1.2815515655446004, 90: 1.6448536269514722, 95: 1.959963984540054}
    for name in spec["models"]:
        for p, point in enumerate(spec["points"]):
            actual = observed[1168:, p]
            mean = saved[name][:, p]
            sigma = sd[name][:, p]
            err = actual - mean
            values = dict(
                mae=sum(abs(float(e)) for e in err) / 293,
                rmse=math.sqrt(sum(float(e) ** 2 for e in err) / 293),
            )
            c = [
                float(e) * math.erf(float(e / s) / math.sqrt(2))
                + float(s)
                * (
                    math.sqrt(2 / math.pi) * math.exp(-(float(e / s) ** 2) / 2)
                    - 1 / math.sqrt(math.pi)
                )
                for e, s in zip(err, sigma)
            ]
            values["crps"] = sum(c) / 293
            row = metric[(metric.model == name) & (metric.point == point)].iloc[0]
            assert row.n == 293 and row.mean_complete and row.probability_complete
            for level, qvalue in quantiles.items():
                low = mean - qvalue * sigma
                high = mean + qvalue * sigma
                values["coverage" + str(level)] = (
                    sum(
                        bool(a >= lo and a <= hi)
                        for a, lo, hi in zip(actual, low, high)
                    )
                    / 293
                )
                values["width" + str(level)] = (
                    sum(float(hi - lo) for lo, hi in zip(low, high)) / 293
                )
                values["interval_score" + str(level)] = (
                    sum(
                        float(
                            hi
                            - lo
                            + max(lo - a, 0) * 2 / (1 - level / 100)
                            + max(a - hi, 0) * 2 / (1 - level / 100)
                        )
                        for a, lo, hi in zip(actual, low, high)
                    )
                    / 293
                )
            for key, value in values.items():
                maximum = max(
                    maximum, close(np.asarray(row[key]), np.asarray(value), 1e-8)
                )
                cells += 1
            curve = source[(source.model == name) & (source.point == point)]
            assert np.array_equal(curve.date, dates[1168:])
            assert np.array_equal(curve.lead_day, np.arange(1, 294))
            for key, expected in [
                ("observed_mm", actual),
                ("mean_mm", mean),
                ("sigma_mm", sigma),
            ]:
                maximum = max(maximum, close(curve[key].to_numpy(), expected, 1e-8))
    save_json(
        out,
        dict(
            passed=True,
            time_utc=now(),
            elapsed_seconds=time.monotonic() - start_time,
            source_hashes=len(spec["source_sha256"]),
            calibration_origins_reproduced=382,
            calibration_model_trajectories=382 * 7,
            final_recursive_blocks_independently_replayed=blocks,
            score_cells=cells,
            forecast_csv_rows=len(source),
            max_mean_difference_mm=max_model,
            max_scale_difference_mm=max_scale,
            max_score_or_csv_difference_mm=maximum,
            forecast_lock_precedes_label_release=True,
            new_fits=0,
            new_optimizer_updates=0,
            calls=CALLS.copy(),
            verifier_sha256=sha(__file__),
        ),
    )
    print(out.read_text(), flush=True)


if __name__ == "__main__":
    main()
