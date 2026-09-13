"""Independently integrate saved C7 CDFs and reconstruct every causal error pool.

Does not import the C7 runner, score functions, model fitting, or physics solver.
"""

import argparse
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def array_sha(x):
    x = np.ascontiguousarray(x)
    return hashlib.sha256(
        str((x.shape, str(x.dtype))).encode() + x.tobytes()
    ).hexdigest()


def read_npz(p):
    with np.load(p, allow_pickle=False) as x:
        return {k: x[k].copy() for k in x.files}


def inverse_cdf(values, probability):
    rank = probability * values.shape[-1]
    n = rank.numerator // rank.denominator
    if rank.denominator == 1 and 0 < n < values.shape[-1]:
        return 0.5 * (values[..., n - 1] + values[..., n])
    return values[..., min(values.shape[-1] - 1, n)]


def independent_scores(y, mu, raw, radii, law):
    error = mu - y
    ans = dict(mae=np.abs(error).mean(axis=0), rmse=np.sqrt(np.mean(error**2, axis=0)))
    if law == "empirical":
        values = np.sort(
            np.concatenate(
                [
                    mu[..., None] - raw[..., None] * radii,
                    mu[..., None] + raw[..., None] * radii,
                ],
                axis=-1,
            ),
            axis=-1,
        )
        # Integrate (F(t)-1{t>=y})^2 over all intervals between support atoms.
        left, right = values[..., :-1], values[..., 1:]
        length = right - left
        below = np.minimum(np.maximum(y[..., None] - left, 0), length)
        f = np.arange(1, values.shape[-1]) / values.shape[-1]
        loss = np.sum(f**2 * below + (1 - f) ** 2 * (length - below), axis=-1)
        loss += np.maximum(values[..., 0] - y, 0) + np.maximum(y - values[..., -1], 0)
    else:
        sigma = raw * np.sqrt(np.mean(radii**2, axis=-1))
        z = np.divide(-error, sigma, out=np.zeros_like(error), where=sigma > 0)
        erf = np.vectorize(math.erf, otypes=[float])
        loss = sigma * (
            z * erf(z / np.sqrt(2))
            + np.sqrt(2 / np.pi) * np.exp(-(z**2) / 2)
            - 1 / np.sqrt(np.pi)
        )
        loss = np.where(sigma > 0, loss, np.abs(error))
    ans["crps"] = loss.mean(axis=0)
    for percent in (80, 90, 95):
        level = Fraction(percent, 100)
        if law == "empirical":
            low, high = (
                inverse_cdf(values, (1 - level) / 2),
                inverse_cdf(values, (1 + level) / 2),
            )
        else:
            q = NormalDist().inv_cdf(float((1 + level) / 2))
            low, high = mu - q * sigma, mu + q * sigma
        ans[f"coverage{percent}"] = ((low <= y) & (y <= high)).mean(axis=0)
        ans[f"width{percent}"] = (high - low).mean(axis=0)
        outside = np.where(y < low, low - y, np.where(y > high, y - high, 0))
        ans[f"interval_score{percent}"] = (
            high - low + 2 / (1 - float(level)) * outside
        ).mean(axis=0)
    return ans


def decision_checks(metrics, summary, candidate, spec):
    h = spec["primary_horizon"]
    eff = spec["effect"]
    group = summary[summary.horizon == h].set_index("model")
    a, b = group.loc[candidate], group.loc["B_ANCHOR"]
    checks = {
        f"average_{k}_improves_5pct": bool(
            a[k] <= (1 - eff["relative_improvement"]) * b[k]
        )
        for k in ("mae", "rmse", "crps", "interval_score90")
    }
    lo, hi = eff["coverage90_average"]
    checks["average_coverage90"] = bool(lo <= a.coverage90 <= hi)
    rows = metrics[metrics.horizon == h].set_index(["model", "point"])
    for point in POINTS:
        pa, pb = rows.loc[(candidate, point)], rows.loc[("B_ANCHOR", point)]
        for k in ("mae", "rmse"):
            checks[f"{point}_{k}_nonregression"] = bool(
                pa[k] <= pb[k] + eff["point_mean_atol_mm"]
            )
        for k in ("crps", "interval_score90"):
            checks[f"{point}_{k}_guard"] = bool(
                pa[k] <= pb[k] * (1 + eff["point_probability_max_regression"])
            )
        lo, hi = eff["coverage90_point"]
        checks[f"{point}_coverage90"] = bool(lo <= pa.coverage90 <= hi)
    for k in ("rmse", "crps"):
        checks[f"beats_simple_{k}"] = bool(
            a[k] <= group.loc[spec["simple_baseline"], k]
        )
    return checks


def verify(run):
    manifest = json.loads((run / "artifact_manifest.json").read_text())["files"]
    for p, wanted in manifest.items():
        if sha(run / p) != wanted:
            raise ValueError("Output artifact changed: " + p)
    source = json.loads((run / "sources.json").read_text())["files"]
    for name, wanted in source.items():
        p = run / "sources" / name
        if not p.exists():
            p = ROOT / name
        if sha(p) != wanted:
            raise ValueError("Source changed: " + name)
    spec = json.loads(
        (run / "sources/config/ootang_rolling_probability.v3_6.json").read_text()
    )
    if not spec["post_transfer_exposure"] or spec["original_single_transfer_reused"]:
        raise ValueError("Exposure status was misrepresented")
    labels = pd.read_csv(ROOT / spec["data"], usecols=[p + "/mm" for p in POINTS])[
        [p + "/mm" for p in POINTS]
    ].to_numpy(float)
    phases = {}
    all_expected = {}
    maximum = 0.0
    score_cells = 0
    pool_cells = 0
    decisions = json.loads((run / "decision.json").read_text())
    for phase, (start, end) in spec["stages"].items():
        config = spec["sources"][phase]
        records = {}
        expected = {"empirical": [], "moment_gaussian": []}
        for p in sorted((run / phase).glob("*.npz")):
            name = p.stem
            pred = read_npz(p)
            records[name] = pred
            current_name = "C4_RIDGE_" + name if name in ("FULL", "DATA") else name
            prior_name = (
                config["prior_" + name.lower() + "_name"]
                if name in ("FULL", "DATA")
                else name
            )
            current = read_npz(
                ROOT
                / config["current_run"]
                / config["current_phase"]
                / (current_name + ".npz")
            )
            prior = read_npz(
                ROOT
                / config["prior_run"]
                / config["prior_phase"]
                / (prior_name + ".npz")
            )
            if not np.array_equal(pred["origins"], np.arange(start, end)):
                raise ValueError("Current origin mismatch")
            for key in ("mean", "raw_sigma", "origins", "teacher_prefixes"):
                np.testing.assert_array_equal(pred[key], current[key], strict=True)
            if (prior["teacher_prefixes"] > prior["origins"]).any() or (
                current["teacher_prefixes"] > start
            ).any():
                raise ValueError("Teacher crossed prefix")
            for k in range(spec["horizons"]):
                valid = pred["origins"] + k < end
                if name != "B_RAW":
                    ids = np.flatnonzero(prior["origins"] + k < start)[
                        -spec["window"] :
                    ]
                    if len(ids) != spec["window"]:
                        raise ValueError("Missing warm calibration predictions")
                    initial = (
                        abs(labels[prior["origins"][ids] + k] - prior["mean"][ids, k])
                        / prior["raw_sigma"][ids, k]
                    )
                    np.testing.assert_array_equal(
                        pred["initial_origins"][k], prior["origins"][ids]
                    )
                    np.testing.assert_array_equal(pred["initial_values"][k], initial.T)
                    count = end - start - k
                    # At origin start+i, only max(0,i-k) current forecasts matured.
                    new = (
                        abs(labels[start + k : end] - pred["mean"][:count, k])
                        / pred["raw_sigma"][:count, k]
                    )
                    history = np.concatenate([initial, new])
                    mature = np.maximum(0, np.arange(end - start) - k)
                    windows = history[
                        mature[:, None] + np.arange(spec["window"])[None, :]
                    ]
                    reconstructed = np.sort(windows, axis=1).transpose(0, 2, 1)
                    saved = pred["radii"][k].transpose(1, 0, 2)
                    np.testing.assert_array_equal(saved, reconstructed)
                    pool_cells += saved.size
                    np.testing.assert_array_equal(
                        pred["final_values"][k], history[-spec["window"] :].T
                    )
                    if pred["updates"][k] != count or pred["last_target"][k] != end - 1:
                        raise ValueError("Final update state mismatch")
                y = labels[pred["origins"][valid] + k]
                mu = pred["mean"][valid, k]
                raw = pred["raw_sigma"][valid, k]
                for law in expected:
                    prefix = "C7_EMP_" if law == "empirical" else "C7_MOMENT_"
                    model = prefix + name if name in ("FULL", "DATA") else name
                    if name == "B_RAW":
                        values = dict(
                            mae=abs(y - mu).mean(axis=0),
                            rmse=np.sqrt(((y - mu) ** 2).mean(axis=0)),
                        )
                    else:
                        values = independent_scores(y, mu, raw, saved[valid], law)
                    for j, point in enumerate(POINTS):
                        expected[law].append(
                            dict(
                                model=model,
                                horizon=k + 1,
                                point=point,
                                n=len(y),
                                **{
                                    key: float(value[j])
                                    for key, value in values.items()
                                },
                            )
                        )
        phases[phase] = records
        for law, rows in expected.items():
            computed = pd.DataFrame(rows)
            path = run / phase / law
            old = pd.read_csv(path / "metrics.csv")
            keys = ["model", "horizon", "point"]
            a = computed.set_index(keys).sort_index()
            b = old.set_index(keys).sort_index()
            np.testing.assert_allclose(
                a.to_numpy(),
                b[a.columns].to_numpy(),
                rtol=5e-11,
                atol=1e-8,
                equal_nan=True,
            )
            delta = np.abs(a.to_numpy() - b[a.columns].to_numpy())
            maximum = max(maximum, float(np.nanmax(delta)))
            score_cells += a.size
            aggregate = []
            for (model, h), g in computed.groupby(["model", "horizon"], sort=False):
                row = dict(model=model, horizon=int(h), n_per_point=int(g.n.iloc[0]))
                row.update(
                    {
                        k: float(g[k].mean())
                        for k in computed.columns
                        if k not in keys + ["n"]
                    }
                )
                row["pooled_rmse"] = float(np.sqrt(np.mean(g.rmse**2)))
                aggregate.append(row)
            summary = pd.DataFrame(aggregate)
            saved_summary = pd.read_csv(path / "summary.csv")
            a = summary.set_index(["model", "horizon"]).sort_index()
            b = saved_summary.set_index(["model", "horizon"]).sort_index()
            np.testing.assert_allclose(
                a.to_numpy(),
                b[a.columns].to_numpy(),
                rtol=5e-11,
                atol=1e-8,
                equal_nan=True,
            )
            maximum = max(
                maximum,
                float(np.nanmax(np.abs(a.to_numpy() - b[a.columns].to_numpy()))),
            )
            score_cells += a.size
            candidate = "C7_EMP_FULL" if law == "empirical" else "C7_MOMENT_FULL"
            checks = decision_checks(computed, summary, candidate, spec)
            d = json.loads((path / "decision.json").read_text())
            if (
                d["checks"] != checks
                or d["passed"] != all(checks.values())
                or d["passed_count"] != sum(checks.values())
            ):
                raise ValueError("Effect decision differs")
            if d != decisions["phases"][phase][law] or d["independent_transfer"]:
                raise ValueError("Aggregate decision or scope differs")
            all_expected[phase + "/" + law] = dict(
                passed=all(checks.values()), passed_count=sum(checks.values())
            )
    chain = ""
    pending = None
    next_origin = {}
    locks = 0
    for raw in (run / "events.jsonl").read_text().splitlines():
        row = json.loads(raw)
        if row["previous_sha256"] != chain:
            raise ValueError("Event hash chain differs")
        chain = hashlib.sha256(raw.encode()).hexdigest()
        phase = row["phase"]
        if row["kind"] == "phase_started":
            next_origin[phase] = spec["stages"][phase][0]
        elif row["kind"] == "forecast_locked":
            if pending is not None or row["origin"] != next_origin[phase]:
                raise ValueError("Forecast order differs")
            n = row["origin"]
            i = n - spec["stages"][phase][0]
            h = min(spec["horizons"], spec["stages"][phase][1] - n)
            wanted = {}
            for name, values in phases[phase].items():
                wanted[name] = dict(
                    mean=array_sha(values["mean"][i, :h]),
                    raw_sigma=array_sha(values["raw_sigma"][i, :h]),
                )
                if name != "B_RAW":
                    wanted[name]["radii"] = array_sha(values["radii"][:h, :, i])
            if wanted != row["forecasts"]:
                raise ValueError("Issued distribution differs from saved arrays")
            pending = (phase, n)
            locks += 1
        elif row["kind"] == "observation_released":
            if pending != (phase, row["index"]) or row["value_sha256"] != array_sha(
                labels[row["index"]]
            ):
                raise ValueError("Observation was released before its forecast lock")
            pending = None
            next_origin[phase] += 1
        elif row["kind"] == "phase_completed":
            if pending is not None or next_origin[phase] != spec["stages"][phase][1]:
                raise ValueError("Incomplete phase")
        else:
            raise ValueError("Unexpected event type")
    if pending is not None or locks != sum(
        end - start for start, end in spec["stages"].values()
    ):
        raise ValueError("Incomplete forecast history")
    return dict(
        passed=True,
        run=str(run),
        checked_utc=datetime.now(timezone.utc).isoformat(),
        immutable_artifacts_checked=len(manifest),
        sources_checked=len(source),
        forecast_locks=locks,
        probability_pool_cells_reconstructed=pool_cells,
        score_cells_checked=score_cells,
        max_csv_rounding_difference=maximum,
        raw_mean_and_sigma_unchanged=True,
        calibration_pool_exact=True,
        empirical_crps_method="independent piecewise CDF integral",
        decisions=all_expected,
        post_transfer_exposure=True,
        independent_transfer=False,
        maximum_label_rows=len(labels),
        new_training_or_optimization=0,
        physical_calls=0,
        verifier_sha256=sha(__file__),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    result = verify(args.run)
    args.out.mkdir(parents=True)
    (args.out / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    (args.out / "verify_ootang_empirical.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
