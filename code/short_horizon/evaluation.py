"""Issued-forecast calibration, frozen feedback controls, and horizon selection."""

from collections import deque
import json
from pathlib import Path
import numpy as np
import pandas as pd

from rolling_probability.innovation import ErrorRidge
from rolling_probability.scoring import score_predictions, aggregate
from .common import ROOT, array_sha
from .data import observations, query


def drift_error_bank(labels, start, h, stop_origin=None, count=90):
    end = start - h if stop_origin is None else min(start - h, stop_origin)
    origins = np.arange(max(2, end - count + 1), end + 1)
    errors = labels[origins + h - 1] - (
        labels[origins - 1] + h * (labels[origins - 1] - labels[origins - 2])
    )
    return origins, errors


class ErrorCalibration:
    def __init__(self, labels, start, previous=None, window=90, floor=1e-6):
        self.window, self.floor = window, floor
        self.pools = []
        self.sources = []
        self.latest = np.full(7, start - 1, int)
        for k in range(7):
            po = np.array([], int)
            pe = np.empty((0, 4))
            if previous is not None:
                ids = previous["origins"] + k
                valid = (ids < start) & np.isfinite(previous["mean"][:, k]).all(axis=1)
                po = previous["origins"][valid][-window:]
                pe = labels[po + k] - previous["mean"][valid, k][-window:]
            stop = int(po[0]) - 1 if len(po) else None
            do, de = (
                drift_error_bank(labels, start, k + 1, stop, window - len(pe))
                if len(pe) < window
                else (np.array([], int), np.empty((0, 4)))
            )
            values = np.concatenate([de, pe])
            if not len(values):
                raise ValueError("No causal scale initialization")
            self.pools.append(deque([v.copy() for v in values], maxlen=window))
            self.sources.append(
                dict(
                    horizon=k + 1,
                    previous_model_count=len(pe),
                    drift_count=len(de),
                    model_origins=po.tolist(),
                    drift_origins=do.tolist(),
                )
            )

    def scale(self, origin):
        if (self.latest >= origin).any():
            raise ValueError("Future error in scale")
        return np.maximum(
            np.stack(
                [np.sqrt(np.mean(np.asarray(p) ** 2, axis=0)) for p in self.pools]
            ),
            self.floor,
        )

    def update(self, k, error, issued_origin, target, next_origin):
        if (
            target != issued_origin + k
            or target >= next_origin
            or target <= self.latest[k]
        ):
            raise ValueError("Unmatured or duplicate calibration error")
        if not np.isfinite(error).all():
            raise ArithmeticError("Nonfinite mature error")
        self.pools[k].append(np.asarray(error).copy())
        self.latest[k] = target


def calibrate(spec, phase, name, mean, cache, previous=None, recorder=None, group=None):
    start, end = spec["stages"][phase]
    labels, _, _ = observations(spec, end)
    origins = np.arange(start, end)
    if mean.shape != (len(origins), 7, 4):
        raise ValueError("Unexpected prediction shape")
    init_labels = labels[:start]
    cal = ErrorCalibration(
        init_labels,
        start,
        previous,
        spec["calibration"]["window"],
        spec["calibration"]["sigma_floor_mm"],
    )
    sigma = np.full_like(mean, np.nan)
    locks = []
    for i, n in enumerate(origins):
        valid = min(7, end - n)
        if not np.isfinite(mean[i, :valid]).all():
            raise ValueError("Missing forecast in legal dates")
        sigma[i, :valid] = cal.scale(n)[:valid]
        locks.append(array_sha(np.stack([mean[i, :valid], sigma[i, :valid]])))
        if recorder:
            recorder.event(
                "forecast_locked",
                model=name,
                group=group,
                origin=int(n),
                last_observed=int(n - 1),
                prediction_sha256=locks[-1],
                feature_history_sha256=str(cache["history_sha"][n - 252]),
            )
        # The current y[n] is only released after this origin's distribution is locked.
        for k in range(7):
            previous_i = i - k
            if previous_i >= 0:
                cal.update(
                    k, labels[n] - mean[previous_i, k], int(n - k), int(n), int(n + 1)
                )
    pred = dict(
        origins=origins,
        mean=mean.copy(),
        sigma=sigma,
        locks=np.asarray(locks),
        teacher_prefixes=cache["teacher"][origins - 252],
    )
    pred["mean"][origins[:, None] + np.arange(7)[None, :] >= end] = np.nan
    return pred, cal.sources


def c16_means(spec, phase, cache):
    if phase not in ("development", "later_exploratory"):
        return {}, {}
    start, end = spec["stages"][phase]
    oldroot = ROOT / "results/ootang_rolling_v3/20260913"
    with np.load(oldroot / "c8_online" / phase / "C8_ONLINE_DATA.npz") as a:
        core = a["mean"][:, :7].copy()
        origins = a["origins"].copy()
    if not np.array_equal(origins, np.arange(start, end)):
        raise ValueError("Frozen core dates differ")
    cfg = json.loads(
        (oldroot / "c16_fast_feedback_run3" / phase / "normalization.json").read_text()
    )
    core_unit = np.asarray(cfg["input_scales"]["C8_ONLINE_DATA"])[:7]
    response = np.asarray(cfg["response_scales"])[:7]
    labels, _, _ = observations(spec, end)
    hs = 612 if phase == "development" else 792
    before = query(cache, np.arange(hs, start))
    physical_unit = np.maximum(
        np.sqrt(np.mean((labels[hs:start] - before["anchor"][:, 0]) ** 2, axis=0)), 1e-6
    )
    anchor = query(cache, origins)["anchor"]
    answers = {}
    audit = {}
    for name, D in (("C16_CORE_RULES", 1), ("C16_PHYS_RULES", 2)):
        state = ErrorRidge(7, D, start, 1.0, feedback_lead_days=1)
        means = core.copy()
        features = np.zeros((len(origins), 7, 4, D))
        betas = np.zeros_like(features)
        for i, n in enumerate(origins):
            valid = min(7, end - n)
            if i:
                features[i, :valid, :, 0] = (
                    labels[n - 1] - core[i - 1, 0]
                ) / core_unit[:valid]
                if D == 2:
                    features[i, :valid, :, 1] = (
                        labels[n - 1] - anchor[i - 1, 0]
                    ) / physical_unit
            betas[i] = state.beta
            means[i, :valid] = core[i, :valid] + response[:valid] * np.einsum(
                "hpd,hpd->hp", state.beta[:valid], features[i, :valid], optimize=False
            )
            for k in range(7):
                j = i - k
                if j >= 1:
                    target = (labels[n] - core[j, k]) / response[k]
                    state.update(
                        k, features[j, k], target, int(n - k), int(n), int(n + 1)
                    )
        answers[name] = means
        audit[name] = dict(
            features=features,
            beta=betas,
            gram=state.gram,
            rhs=state.rhs,
            core_unit=core_unit,
            response_unit=response,
            physical_unit=physical_unit,
        )
    with np.load(oldroot / "c16_fast_feedback_run3" / phase / "C16_FAST_CORE.npz") as a:
        old = a["mean"][:, :7]
        difference = float(np.nanmax(abs(old - answers["C16_CORE_RULES"])))
        if difference > 1e-8:
            raise ArithmeticError("Frozen C16 CORE mean changed: " + str(difference))
    audit["core_replay_max_difference_mm"] = difference
    return answers, audit


def score_set(spec, phase, predictions, directory, common=False):
    start, end = spec["stages"][phase]
    labels, _, _ = observations(spec, end)
    frames = []
    for name, pred in predictions.items():
        item = pred
        if common:
            mask = pred["origins"] + 7 <= end
            item = {
                k: v[mask] for k, v in pred.items() if k in ("origins", "mean", "sigma")
            }
        frame = score_predictions(item, labels, levels=spec["calibration"]["levels"])
        frame.insert(0, "model", name)
        frames.append(frame)
    metrics = pd.concat(frames, ignore_index=True)
    summary = aggregate(metrics)
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    suffix = "_common" if common else ""
    metrics.to_csv(
        path / ("metrics_by_point_horizon" + suffix + ".csv"),
        index=False,
        float_format="%.15g",
    )
    summary.to_csv(
        path / ("summary_by_horizon" + suffix + ".csv"),
        index=False,
        float_format="%.15g",
    )
    return metrics, summary


def quality(summary, name):
    s = summary.set_index(["model", "horizon"])
    return float(
        np.mean(
            [
                0.5
                * (
                    s.loc[(name, h), "rmse"] / max(s.loc[("DRIFT1", h), "rmse"], 1e-8)
                    + s.loc[(name, h), "crps"] / max(s.loc[("DRIFT1", h), "crps"], 1e-8)
                )
                for h in range(1, 8)
            ]
        )
    )


def gates(spec, metrics, summary, name, h, physics_pass=True):
    s = summary[summary.horizon == h].set_index("model")
    m = metrics[metrics.horizon == h].set_index(["model", "point"])
    c, b, d = s.loc[name], s.loc["B_ANCHOR"], s.loc["DRIFT1"]
    tol = spec["selection"]["tolerance_mm"]
    checks = {
        f"mean_{k}_better_B": bool(c[k] < b[k] - tol)
        for k in ("mae", "rmse", "crps", "interval_score90")
    }
    checks.update(
        {f"no_worse_drift_{k}": bool(c[k] <= d[k] + tol) for k in ("rmse", "crps")}
    )
    checks["average_coverage90"] = bool(0.85 <= c.coverage90 <= 0.95)
    for p in spec["points"]:
        a, base = m.loc[(name, p)], m.loc[("B_ANCHOR", p)]
        for k in ("mae", "rmse"):
            checks[p + "_" + k] = bool(a[k] <= base[k] + tol)
        for k in ("crps", "interval_score90"):
            checks[p + "_" + k] = bool(a[k] <= 1.05 * base[k] + tol)
        checks[p + "_coverage90"] = bool(0.8 <= a.coverage90 <= 0.98)
    checks["physical_contract"] = bool(physics_pass)
    return dict(passed=all(checks.values()), checks=checks)


def selection(spec, metrics, summary, physical_pass=None):
    physical_pass = physical_pass or {}
    excluded = set(spec["selection_excluded"])
    names = sorted(set(summary.model) - excluded)
    result = []
    for h in range(1, 8):
        s = summary[summary.horizon == h].set_index("model")
        tol = spec["selection"]["tolerance_mm"]

        def winner(keys, candidates):
            left = list(candidates)
            for k in keys:
                best = min(float(s.loc[n, k]) for n in left)
                left = [n for n in left if float(s.loc[n, k]) <= best + tol]
            return sorted(left)[0]

        gm = {
            n: gates(spec, metrics, summary, n, h, physical_pass.get(n, True))
            for n in names
        }
        good = [n for n in names if gm[n]["passed"]]
        q = {
            n: float(
                0.5
                * (
                    s.loc[n, "rmse"] / max(s.loc["DRIFT1", "rmse"], tol)
                    + s.loc[n, "crps"] / max(s.loc["DRIFT1", "crps"], tol)
                )
            )
            for n in names
        }
        recommended = None
        if good:
            v = min(q[n] for n in good)
            recommended = sorted(n for n in good if q[n] <= v + tol)[0]
        result.append(
            dict(
                horizon=h,
                mean_best=winner(["rmse", "mae"], names),
                probability_best=winner(["crps", "interval_score90"], names),
                recommended=recommended,
                q=q,
                gates=gm,
            )
        )
    return result
