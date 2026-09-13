"""Independently verify mature mixture weights, CDFs and proper scoring.

Uses Gaussian component energy expectations, plus separate CDF quadrature.
Does not import the production mixture learner or scorer.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd
from scipy.integrate import quad
from scipy.special import erf

from verify_ootang_empirical import decision_checks
from verify_ootang_rolling import independent_metrics

ROOT = Path(__file__).resolve().parents[1]
POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def array_sha(x):
    x = np.ascontiguousarray(x)
    return hashlib.sha256(
        str((x.shape, str(x.dtype))).encode() + x.tobytes()
    ).hexdigest()


def arrays(path):
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k].copy() for k in z.files}


def component_absolute(error, scale):
    t = error / scale
    erf = np.vectorize(math.erf, otypes=[float])
    return scale * (t * erf(t / np.sqrt(2)) + np.sqrt(2 / np.pi) * np.exp(-t * t / 2))


def energy_score(error, core, wide, weight):
    scales = [core, wide]
    weights = [1 - weight, weight]
    answer = sum(w * component_absolute(error, s) for w, s in zip(weights, scales))
    for i in range(2):
        for j in range(2):
            distance = np.sqrt(2 / np.pi) * np.hypot(scales[i], scales[j])
            answer -= weights[i] * weights[j] * distance / 2
    return answer


def independent_coefficients(error, core, wide):
    first = component_absolute(error, core)
    second = component_absolute(error, wide)
    cross = np.sqrt(2) * np.hypot(core, wide)
    linear = second - first + (2 * core - cross) / np.sqrt(np.pi)
    quadratic = ((wide - core) / (cross + core + wide)) * (
        (wide - core) / np.sqrt(np.pi)
    )
    return linear, quadratic


def fitted_weight(linear, quadratic):
    L, Q = np.sum(linear, axis=0), np.sum(quadratic, axis=0)
    return np.clip(np.divide(-L, 2 * Q, out=np.zeros(4), where=Q > 0), 0, 1)


def interval_grid_losses(error, core, wide, spec):
    grid = np.arange(spec["weight_grid_intervals"] + 1, dtype=float)
    grid /= spec["weight_grid_intervals"]
    level = spec["weight_score_level"]
    left = np.zeros((*core.shape, len(grid)))
    right = np.broadcast_to(
        wide[..., None] * NormalDist().inv_cdf((1 + level) / 2), left.shape
    ).copy()
    for _ in range(spec["quantile_bisection_steps"]):
        x = left + (right - left) * 0.5
        centered_cdf = (1 - grid) * erf(x / (core[..., None] * np.sqrt(2)))
        centered_cdf += grid * erf(x / (wide[..., None] * np.sqrt(2)))
        right = np.where(centered_cdf >= level, x, right)
        left = np.where(centered_cdf < level, x, left)
    radius = left + (right - left) * 0.5
    return 2 * radius + (2 / (1 - level)) * np.clip(
        np.abs(error[..., None]) - radius, 0, None
    ), grid


def interval_weight(average, grid, spec):
    lowest = np.min(average, axis=-1)
    allowance = spec["weight_tie_relative"] * np.maximum(1, np.abs(lowest))
    selected = np.empty(4)
    for p in range(4):
        selected[p] = grid[np.flatnonzero(average[p] <= lowest[p] + allowance[p])[0]]
    return selected


def verify_mature_weights(spec, state, name, k, error, c, s, w, end, totals, maximum):
    count = len(error)
    begin_final = max(0, count - spec["mixture_window"])
    reconstructed = np.zeros_like(w)
    if spec.get("weight_objective", "crps") == "interval_score":
        losses, grid = interval_grid_losses(error, c, s, spec)
        np.testing.assert_array_equal(grid, state[name]["grid"])
        for i in range(count):
            stop = max(0, i - k)
            begin = max(0, stop - spec["mixture_window"])
            if stop:
                reconstructed[i] = interval_weight(
                    losses[begin:stop].mean(axis=0), grid, spec
                )
        average = losses[begin_final:].mean(axis=0)
        np.testing.assert_allclose(
            average, state[name]["mean_loss_grid"][k], rtol=5e-11, atol=1e-8
        )
        maximum["interval_loss_grid_mm"] = max(
            maximum.get("interval_loss_grid_mm", 0),
            float(abs(average - state[name]["mean_loss_grid"][k]).max()),
        )
        np.testing.assert_array_equal(w, reconstructed)
        np.testing.assert_array_equal(
            interval_weight(average, grid, spec), state[name]["weights"][k]
        )
        if state[name]["pool_counts"][k] != count - begin_final:
            raise ValueError("Interval risk pool length differs")
        totals["grid_loss_values"] = totals.get("grid_loss_values", 0) + losses.size
    else:
        L, Q = independent_coefficients(error, c, s)
        for i in range(count):
            stop = max(0, i - k)
            begin = max(0, stop - spec["mixture_window"])
            reconstructed[i] = fitted_weight(L[begin:stop], Q[begin:stop])
        pool = np.stack([L[begin_final:], Q[begin_final:]], axis=1)
        actual = np.asarray(state[name]["coefficient_pools"][k])
        np.testing.assert_allclose(pool, actual, rtol=5e-11, atol=1e-8)
        maximum["coefficient_pool"] = max(
            maximum["coefficient_pool"], float(abs(pool - actual).max())
        )
        np.testing.assert_allclose(
            fitted_weight(L[begin_final:], Q[begin_final:]),
            state[name]["weights"][k],
            rtol=5e-11,
            atol=1e-8,
        )
        np.testing.assert_allclose(w, reconstructed, rtol=5e-11, atol=1e-8)
    maximum["weight"] = max(maximum["weight"], float(abs(w - reconstructed).max()))
    if state[name]["updates"][k] != count or state[name]["last_target"][k] != end - 1:
        raise ValueError("Mixture maturation counts differ")
    totals["weight_updates"] += count * 4


def integrated_score(error, core, wide, weight):
    standard = NormalDist()

    def cdf(t):
        return (1 - weight) * standard.cdf(t / core) + weight * standard.cdf(t / wide)

    limit = max(16 * wide, abs(error) + wide)
    cuts = sorted(set([-limit, -wide, -core, 0.0, error, core, wide, limit]))
    total = 0.0

    def below(t):
        return cdf(t) ** 2

    def above(t):
        return (1 - cdf(t)) ** 2

    for a, b in zip(cuts[:-1], cuts[1:]):
        f = below if b <= error else above
        total += quad(f, a, b, epsabs=1e-11, epsrel=1e-11, limit=200)[0]
    # Omitted normal tails beyond 16*wide have an absolute bound < 1e-55*wide.
    return total


def verify(run):
    manifest = json.loads((run / "artifact_manifest.json").read_text())["files"]
    for name, wanted in manifest.items():
        if sha(run / name) != wanted:
            raise ValueError("Artifact changed: " + name)
    source = json.loads((run / "sources.json").read_text())["files"]
    for name, wanted in source.items():
        p = run / "sources" / name
        if not p.exists():
            p = ROOT / name
        if sha(p) != wanted:
            raise ValueError("Source changed: " + name)
    configs = list((run / "sources/config").glob("ootang_rolling_probability.*.json"))
    if len(configs) != 1:
        raise ValueError("Ambiguous frozen config")
    spec = json.loads(configs[0].read_text())
    if not spec["post_transfer_exposure"] or spec["original_single_transfer_reused"]:
        raise ValueError("Exposure boundary changed")
    status = json.loads((run / "status.json").read_text())
    if (
        status["state"] != "completed"
        or status["exit_code"] != 0
        or datetime.fromisoformat(status["finished_utc"])
        > datetime.fromisoformat(spec["deadline_utc"])
    ):
        raise ValueError("Incomplete or late run")
    prior = ROOT / spec["prior_verification"]
    if (
        sha(prior) != spec["prior_verification_sha256"]
        or not json.loads(prior.read_text())["passed"]
    ):
        raise ValueError("C8 causal verification changed")
    labels = pd.read_csv(ROOT / spec["data"], usecols=[p + "/mm" for p in POINTS])[
        [p + "/mm" for p in POINTS]
    ].to_numpy(float)
    if sha(ROOT / spec["data"]) != spec["data_sha256"]:
        raise ValueError("Original observations changed")
    decisions = json.loads((run / "decision.json").read_text())
    records = {}
    totals = dict(
        weight_updates=0, score_cells=0, quadrature_checks=0, cdf_values_checked=0
    )
    maximum = dict(
        weight=0.0,
        wide_scale_mm=0.0,
        score_csv_mm=0.0,
        cdf=0.0,
        quadrature_mm=0.0,
        coefficient_pool=0.0,
    )
    for phase, (start, end) in spec["stages"].items():
        ref, directory = spec["sources"][phase], run / phase
        H, N = spec["horizons"], end - start
        original_root = ROOT / ref["current_run"] / ref["current_phase"]
        if (
            sha(ROOT / ref["current_run"] / "artifact_manifest.json")
            != ref["current_manifest_sha256"]
        ):
            raise ValueError("Original C8 identity changed")
        core = arrays(original_root / (spec["core_model"] + ".npz"))
        current = {p.stem: arrays(p) for p in directory.glob("*.npz")}
        records[phase] = current
        state = json.loads((directory / "mixture_state.json").read_text())
        rows = []
        for name, pred in current.items():
            np.testing.assert_array_equal(pred["origins"], np.arange(start, end))
            np.testing.assert_array_equal(pred["teacher_prefixes"], np.full(N, start))
            learned_here = name in spec["reference_models"]
            is_mixture = {"core_sigma", "wide_sigma", "weight"} <= pred.keys()
            if learned_here:
                for key in ("origins", "teacher_prefixes", "mean", "raw_sigma"):
                    np.testing.assert_array_equal(pred[key], core[key])
                for key, old_key in (
                    ("core_sigma", "sigma"),
                    ("core_calibration_factor", "calibration_factor"),
                    ("core_feedback_log_scale", "feedback_log_scale"),
                ):
                    np.testing.assert_array_equal(pred[key], core[old_key])
                reference = arrays(
                    original_root / (spec["reference_models"][name] + ".npz")
                )
                wide = np.maximum(
                    core["sigma"],
                    np.hypot(reference["sigma"], reference["mean"] - core["mean"]),
                )
                np.testing.assert_allclose(
                    pred["wide_sigma"], wide, rtol=5e-11, atol=1e-8, equal_nan=True
                )
                maximum["wide_scale_mm"] = max(
                    maximum["wide_scale_mm"],
                    float(np.nanmax(abs(wide - pred["wide_sigma"]))),
                )
                moment = np.sqrt(
                    (1 - pred["weight"]) * pred["core_sigma"] ** 2
                    + pred["weight"] * pred["wide_sigma"] ** 2
                )
                np.testing.assert_allclose(
                    pred["sigma"], moment, rtol=5e-11, atol=1e-8, equal_nan=True
                )
            else:
                old = arrays(original_root / (name + ".npz"))
                if old.keys() != pred.keys():
                    raise ValueError("Original distribution fields changed")
                for key in old:
                    np.testing.assert_array_equal(pred[key], old[key])
            for k in range(H):
                count = N - k
                y, mu = labels[start + k : end], pred["mean"][:count, k]
                error = y - mu
                if not is_mixture:
                    values = (
                        dict(
                            mae=abs(error).mean(axis=0),
                            rmse=np.sqrt(np.mean(error**2, axis=0)),
                        )
                        if name == "B_RAW"
                        else independent_metrics(y, mu, pred["sigma"][:count, k])
                    )
                else:
                    c, s, w = (
                        pred[key][:count, k]
                        for key in ("core_sigma", "wide_sigma", "weight")
                    )
                    if (
                        not np.isfinite(np.stack([c, s, w])).all()
                        or (c <= 0).any()
                        or (s < c).any()
                        or (w < 0).any()
                        or (w > 1).any()
                    ):
                        raise ValueError("Invalid issued mixture")
                    if learned_here:
                        verify_mature_weights(
                            spec, state, name, k, error, c, s, w, end, totals, maximum
                        )
                    loss = energy_score(error, c, s, w)
                    values = dict(
                        mae=abs(error).mean(axis=0),
                        rmse=np.sqrt(np.mean(error**2, axis=0)),
                        crps=loss.mean(axis=0),
                    )
                    normal_cdf = np.vectorize(NormalDist().cdf, otypes=[float])
                    for level in spec["probability"]["levels"]:
                        percent = round(100 * level)
                        low, high = (
                            pred[f"lower{percent}"][:count, k],
                            pred[f"upper{percent}"][:count, k],
                        )
                        np.testing.assert_allclose(
                            (high + low) / 2, mu, rtol=5e-11, atol=1e-8
                        )
                        for bound, expected in (
                            (low, (1 - level) / 2),
                            (high, (1 + level) / 2),
                        ):
                            cdf = (1 - w) * normal_cdf(
                                (bound - mu) / c
                            ) + w * normal_cdf((bound - mu) / s)
                            np.testing.assert_allclose(
                                cdf, expected, rtol=0, atol=1e-10
                            )
                            maximum["cdf"] = max(
                                maximum["cdf"], float(abs(cdf - expected).max())
                            )
                            totals["cdf_values_checked"] += cdf.size
                        width = high - low
                        missed = np.where(
                            y < low, low - y, np.where(y > high, y - high, 0)
                        )
                        values[f"coverage{percent}"] = ((low <= y) & (y <= high)).mean(
                            axis=0
                        )
                        values[f"width{percent}"] = width.mean(axis=0)
                        values[f"interval_score{percent}"] = (
                            width + 2 * missed / (1 - level)
                        ).mean(axis=0)
                    if k + 1 == spec["primary_horizon"]:
                        for i in sorted(set(range(0, count, 30)) | {count - 1}):
                            for p in range(4):
                                integral = integrated_score(
                                    float(error[i, p]),
                                    float(c[i, p]),
                                    float(s[i, p]),
                                    float(w[i, p]),
                                )
                                np.testing.assert_allclose(
                                    integral, loss[i, p], rtol=5e-11, atol=1e-8
                                )
                                maximum["quadrature_mm"] = max(
                                    maximum["quadrature_mm"],
                                    float(abs(integral - loss[i, p])),
                                )
                                totals["quadrature_checks"] += 1
                for j, point in enumerate(POINTS):
                    rows.append(
                        dict(
                            model=name,
                            horizon=k + 1,
                            point=point,
                            n=count,
                            **{key: float(value[j]) for key, value in values.items()},
                        )
                    )
        computed = pd.DataFrame(rows)
        summary_rows = []
        for (name, h), group in computed.groupby(["model", "horizon"], sort=False):
            summary_rows.append(
                dict(
                    model=name,
                    horizon=int(h),
                    n_per_point=int(group.n.iloc[0]),
                    **{
                        key: float(group[key].mean())
                        for key in computed
                        if key not in ("model", "horizon", "point", "n")
                    },
                    pooled_rmse=float(np.sqrt(np.mean(group.rmse**2))),
                )
            )
        summary = pd.DataFrame(summary_rows)
        for frame, filename, keys in (
            (computed, "metrics.csv", ["model", "horizon", "point"]),
            (summary, "summary.csv", ["model", "horizon"]),
        ):
            a = frame.set_index(keys).sort_index()
            b = pd.read_csv(directory / filename).set_index(keys).sort_index()
            np.testing.assert_allclose(
                a.to_numpy(),
                b[a.columns].to_numpy(),
                rtol=5e-11,
                atol=1e-8,
                equal_nan=True,
            )
            maximum["score_csv_mm"] = max(
                maximum["score_csv_mm"],
                float(np.nanmax(abs(a.to_numpy() - b[a.columns].to_numpy()))),
            )
            totals["score_cells"] += a.size
        checks = decision_checks(computed, summary, spec["candidate"], spec)
        decision = json.loads((directory / "decision.json").read_text())
        if (
            checks != decision["checks"]
            or decision["passed"] != all(checks.values())
            or decision["passed_count"] != sum(checks.values())
            or decision != decisions["phases"][phase]
        ):
            raise ValueError("Effect decision differs")
    chain, pending, locks = "", None, 0
    next_origin = {}
    for raw in (run / "events.jsonl").read_text().splitlines():
        e = json.loads(raw)
        if e["previous_sha256"] != chain:
            raise ValueError("Event chain differs")
        chain = hashlib.sha256(raw.encode()).hexdigest()
        phase = e["phase"]
        start, end = spec["stages"][phase]
        if e["kind"] == "phase_started":
            if e["inherited_core"] != spec["core_model"]:
                raise ValueError("Core identity changed")
            next_origin[phase] = start
        elif e["kind"] == "forecast_locked":
            n = e["origin"]
            i, h = n - start, min(spec["horizons"], end - n)
            if (
                pending is not None
                or next_origin[phase] != n
                or e["history_sha256"] != array_sha(labels[:n])
            ):
                raise ValueError("Incorrect prediction order or history")
            expected = {
                name: array_sha(
                    np.stack([pred[k][i, :h] for k in ("mean", "sigma", "raw_sigma")])
                )
                for name, pred in records[phase].items()
            }
            components = {
                name: array_sha(
                    np.stack(
                        [
                            records[phase][name][k][i, :h]
                            for k in ("mean", "core_sigma", "wide_sigma", "weight")
                        ]
                    )
                )
                for name in spec["reference_models"]
            }
            if expected != e["predictions"] or components != e["mixture_parameters"]:
                raise ValueError("Issued distribution lock differs")
            pending = (phase, n)
            locks += 1
        elif e["kind"] == "observation_released":
            if pending != (phase, e["index"]) or e["value_sha256"] != array_sha(
                labels[e["index"]]
            ):
                raise ValueError("Target released before forecast")
            pending = None
            next_origin[phase] += 1
        elif e["kind"] == "phase_completed":
            if pending is not None or next_origin[phase] != end:
                raise ValueError("Incomplete phase")
        else:
            raise ValueError("Unknown event")
    if (
        locks != sum(e - s for s, e in spec["stages"].values())
        or pending is not None
        or totals["weight_updates"] != decisions["mature_point_updates"]
        or totals["weight_updates"] != spec["expected_mature_point_updates"]
    ):
        raise ValueError("Incomplete predictions or weight updates")
    if spec.get("weight_objective") == "interval_score":
        if (
            totals["grid_loss_values"] != spec["expected_grid_loss_values"]
            or totals["grid_loss_values"] != decisions["grid_loss_values"]
        ):
            raise ValueError("Grid loss accounting differs")
    if any(
        decisions[k] != 0
        for k in (
            "new_mean_updates",
            "new_neural_updates",
            "new_scale_optimizations",
            "physical_calls",
        )
    ):
        raise ValueError("Budget accounting changed")
    return dict(
        passed=True,
        checked_utc=datetime.now(timezone.utc).isoformat(),
        run=str(run),
        immutable_artifacts_checked=len(manifest),
        sources_checked=len(source),
        forecast_locks=locks,
        checks=totals,
        maximum_differences=maximum,
        old_distributions_and_core_exactly_unchanged=True,
        real_mixture_cdf_and_crps_verified=True,
        post_exposure=True,
        independent_transfer=False,
        new_training_updates=0,
        physical_calls=0,
        verifier_sha256=sha(__file__),
        dependency_sha256={
            name: sha(Path(__file__).with_name(name))
            for name in ("verify_ootang_empirical.py", "verify_ootang_rolling.py")
        },
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
    for name in (
        Path(__file__).name,
        "verify_ootang_empirical.py",
        "verify_ootang_rolling.py",
    ):
        (args.out / name).write_bytes(Path(__file__).with_name(name).read_bytes())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
