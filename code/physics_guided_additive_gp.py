"""Frozen grouped ADD/TIME_ONLY GP experiment, sharing immutable v1 data/scoring."""

import json

import numpy as np
import pandas as pd
from scipy.linalg import solve_triangular
from scipy.spatial.distance import cdist
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import (
    ConstantKernel,
    Hyperparameter,
    Kernel,
    Matern,
    WhiteKernel,
)

from physics_guided_gp import (
    POINTS,
    ROOT,
    check_historical_scores,
    check_sources,
    load_reference,
    read_npz,
    score_distributions,
    sha,
    specification as old_specification,
)

CONFIG = ROOT / "config/ootang_bplus_additive_gp.v2.json"
CONFIG_SHA = "3cdf32187b42edac3fd2bf1bd3cb0173ef173e8af9a05e5648e819fe3ecff885"
ARMS = ("TIME_ONLY", "ADD")
STRATEGIES = ("M0", "v1.1-e0", "BPLUS_GP_V1", *ARMS)


def specification():
    old_specification()  # Frozen dependencies and sources; no training or labels.
    if sha(CONFIG) != CONFIG_SHA:
        raise ValueError("Frozen v2 configuration changed")
    spec = json.loads(CONFIG.read_text())
    check_sources(spec["source_sha256"])
    return spec


def load_sources(spec):
    saved, old, audit = load_reference(spec)
    directory = ROOT / spec["old_gp_dir"]
    index = json.loads((ROOT / spec["old_gp_manifest"]).read_text())["files"]
    for path, record in index.items():
        if sha(directory / path) != record["sha256"]:
            raise ValueError("Historical GP artifact changed: " + path)
    lock = json.loads((ROOT / spec["old_gp_lock"]).read_text())
    for path, digest in lock["files"].items():
        if sha(directory / path) != digest:
            raise ValueError("Historical GP prediction lock changed")
    historical = read_npz(ROOT / spec["old_gp_predictions"])
    np.testing.assert_array_equal(historical["dates"], saved["dates"])
    for key in ("mean", "latent_variance", "observation_variance", "noise_variance"):
        a = historical[key]
        if a.shape != (1168, 4) or not np.isfinite(a).all():
            raise ValueError("Historical GP field invalid: " + key)
    if (historical["observation_variance"] <= 0).any():
        raise ValueError("Historical GP observation variance invalid")
    audit["old_gp_prediction_lock_sha256"] = sha(ROOT / spec["old_gp_lock"])
    audit["old_gp_retrained"] = False
    return saved, old, historical, audit


class SelectedKernel(Kernel):
    """Cloneable sklearn kernel restricted to registered input columns."""

    def __init__(self, kernel, active_dims):
        self.kernel = kernel
        self.active_dims = active_dims

    @property
    def theta(self):
        return self.kernel.theta

    @theta.setter
    def theta(self, value):
        self.kernel.theta = value

    @property
    def bounds(self):
        return self.kernel.bounds

    @property
    def hyperparameters(self):
        return [
            Hyperparameter(
                "kernel__" + h.name, h.value_type, h.bounds, h.n_elements, h.fixed
            )
            for h in self.kernel.hyperparameters
        ]

    def __call__(self, X, Y=None, eval_gradient=False):
        x = np.asarray(X)[:, self.active_dims]
        y = None if Y is None else np.asarray(Y)[:, self.active_dims]
        return self.kernel(x, y, eval_gradient=eval_gradient)

    def diag(self, X):
        return self.kernel.diag(np.asarray(X)[:, self.active_dims])

    def is_stationary(self):
        return self.kernel.is_stationary()


def kernel(spec, arm):
    if arm not in ARMS:
        raise ValueError("Only the two registered arms exist")
    k = spec["kernel"]
    time = SelectedKernel(
        ConstantKernel(k["time_amplitude"], tuple(k["amplitude_bounds"]))
        * Matern(k["time_length_scale"], tuple(k["length_bounds"]), nu=k["nu"]),
        tuple(spec["groups"]["time"]),
    )
    noise = WhiteKernel(k["noise_variance"], tuple(k["noise_bounds"]))
    if arm == "TIME_ONLY":
        return time + noise
    physical = SelectedKernel(
        ConstantKernel(k["physical_amplitude"], tuple(k["amplitude_bounds"]))
        * Matern(k["physical_length_scales"], tuple(k["length_bounds"]), nu=k["nu"]),
        tuple(spec["groups"]["physical"]),
    )
    return time + physical + noise


def new_gp(spec, arm, optimizer):
    return GaussianProcessRegressor(
        kernel=kernel(spec, arm),
        alpha=spec["jitter"],
        optimizer=optimizer,
        normalize_y=False,
        n_restarts_optimizer=0,
        copy_X_train=True,
        random_state=0,
    )


def components(model, arm):
    if arm == "TIME_ONLY":
        return model.kernel_.k1, None, model.kernel_.k2
    if arm == "ADD":
        return model.kernel_.k1.k1, model.kernel_.k1.k2, model.kernel_.k2
    raise ValueError("Unregistered arm")


def predict(model, arm, x, base, scale, spec):
    mu, std = model.predict(x, return_std=True)
    kt, kh, kn = components(model, arm)
    t = kt(x, model.X_train_)
    h = kh(x, model.X_train_) if kh is not None else np.zeros_like(t)
    vt = solve_triangular(model.L_, t.T, lower=True, check_finite=False)
    vh = solve_triangular(model.L_, h.T, lower=True, check_finite=False)
    mt = np.einsum("ij,j->i", t, model.alpha_, optimize=False)
    mh = np.einsum("ij,j->i", h, model.alpha_, optimize=False)
    time_variance = kt.diag(x) - np.einsum("ij,ij->j", vt, vt)
    physical_variance = (
        kh.diag(x) if kh is not None else np.zeros(len(x))
    ) - np.einsum("ij,ij->j", vh, vh)
    cross = -np.einsum("ij,ij->j", vt, vh)
    total_v = vt + vh
    amplitude_t = float(kt.kernel.k1.constant_value)
    amplitude_h = float(kh.kernel.k1.constant_value) if kh is not None else 0.0
    noise = float(kn.noise_level)
    latent = amplitude_t + amplitude_h - np.einsum("ij,ij->j", total_v, total_v)
    bound = spec["negative_variance_factor"] * max(1.0, amplitude_t + amplitude_h)
    for name, value in (
        ("time", time_variance),
        ("physical", physical_variance),
        ("total", latent),
    ):
        if not np.isfinite(value).all() or value.min() < -bound:
            raise ArithmeticError(name + " variance violates the fixed roundoff rule")
    raw_latent_min = float(latent.min())
    # Check the signed cross term before permitted roundoff cleanup.
    np.testing.assert_allclose(
        latent * scale**2,
        (time_variance + physical_variance + 2 * cross) * scale**2,
        atol=spec["matrix_atol"],
        rtol=spec["matrix_rtol"],
    )
    np.testing.assert_allclose(
        base + mu * scale,
        base + (mt + mh) * scale,
        atol=spec["matrix_atol"],
        rtol=spec["matrix_rtol"],
    )
    np.testing.assert_allclose(
        std**2 * scale**2,
        (latent + noise) * scale**2,
        atol=spec["matrix_atol"],
        rtol=spec["matrix_rtol"],
    )
    result = dict(
        mean=base + mu * scale,
        residual_mean=mu * scale,
        time_mean=mt * scale,
        physical_mean=mh * scale,
        time_variance=np.maximum(time_variance, 0) * scale**2,
        physical_variance=np.maximum(physical_variance, 0) * scale**2,
        cross_covariance=cross * scale**2,
        latent_variance=np.maximum(latent, 0) * scale**2,
        observation_variance=std**2 * scale**2,
        noise_variance=np.full(len(x), noise * scale**2),
    )
    if (
        not all(np.isfinite(v).all() for v in result.values())
        or (result["observation_variance"] <= 0).any()
    ):
        raise ArithmeticError("Nonfinite or invalid full prediction")
    audit = dict(
        time_amplitude=amplitude_t,
        physical_amplitude=amplitude_h,
        time_length_scale=float(kt.kernel.k2.length_scale),
        physical_length_scales=np.asarray(kh.kernel.k2.length_scale).tolist()
        if kh is not None
        else [],
        normalized_noise_variance=noise,
        negative_bound=bound,
        raw_latent_min=raw_latent_min,
        rounded_counts=dict(
            time=int(sum(time_variance < 0)),
            physical=int(sum(physical_variance < 0)),
            total=int(sum(latent < 0)),
        ),
        component_mean_max_error_mm=float(abs(mu - mt - mh).max() * scale),
        covariance_identity_max_error_mm2=float(
            abs(latent - time_variance - physical_variance - 2 * cross).max() * scale**2
        ),
    )
    return result, audit


def independent_posterior(x_train, target, x, params, jitter):
    """Manual selected Matérn kernels and LU solves, independent of sklearn and Cholesky."""

    def matern(a, b, lengths, amplitude):
        d = np.sqrt(3 * cdist(a / lengths, b / lengths, "sqeuclidean"))
        return amplitude * (1 + d) * np.exp(-d)

    def parts(a, b):
        t = matern(
            a[:, :1], b[:, :1], params["time_length_scale"], params["time_amplitude"]
        )
        h = (
            matern(
                a[:, 1:],
                b[:, 1:],
                np.asarray(params["physical_length_scales"]),
                params["physical_amplitude"],
            )
            if params["physical_amplitude"]
            else np.zeros_like(t)
        )
        return t, h

    tt, hh = parts(x_train, x_train)
    noise = params["normalized_noise_variance"]
    c = tt + hh + (noise + jitter) * np.eye(len(x_train))
    t, h = parts(x, x_train)
    weights = np.linalg.solve(c, target)
    solved = np.linalg.solve(c, np.concatenate((t.T, h.T), axis=1))
    st, sh = np.split(solved, 2, axis=1)
    mt = np.einsum("ij,j->i", t, weights)
    mh = np.einsum("ij,j->i", h, weights)
    v_t = params["time_amplitude"] - np.einsum("ij,ji->i", t, st)
    v_h = params["physical_amplitude"] - np.einsum("ij,ji->i", h, sh)
    cross = -np.einsum("ij,ji->i", t, sh)
    v = (
        params["time_amplitude"]
        + params["physical_amplitude"]
        - np.einsum("ij,ji->i", t + h, st + sh)
    )
    return dict(
        residual_mean=mt + mh,
        time_mean=mt,
        physical_mean=mh,
        time_variance=v_t,
        physical_variance=v_h,
        cross_covariance=cross,
        latent_variance=v,
        observation_variance=v + noise,
        noise_variance=np.full(len(x), noise),
    )


def score_all(saved, old, historical, predictions, labels):
    output = [[], [], []]
    for name, pred in (
        ("BPLUS_GP_V1", historical),
        *((a, predictions[a]) for a in ARMS),
    ):
        tables = score_distributions(saved, old, pred, labels)
        for i, frame in enumerate(tables):
            if name != "BPLUS_GP_V1":
                frame = frame[frame.strategy == "BPLUS_GP"].copy()
            else:
                frame = frame.copy()
            frame["strategy"] = frame.strategy.replace({"BPLUS_GP": name})
            output[i].append(frame)
    return tuple(pd.concat(frames, ignore_index=True) for frames in output)


def comparisons(metrics):
    rows = []
    indexed = metrics.set_index(["strategy", "part", "station"])
    columns = [c for c in metrics.select_dtypes(include="number") if c != "days"]
    for arm in ARMS:
        for ref in (
            "M0",
            "v1.1-e0",
            "BPLUS_GP_V1",
            *(["TIME_ONLY"] if arm == "ADD" else []),
        ):
            for phase in ("train", "prediction"):
                for point in POINTS:
                    a, b = (
                        indexed.loc[(arm, phase, point)],
                        indexed.loc[(ref, phase, point)],
                    )
                    for column in columns:
                        if pd.isna(b[column]):
                            continue
                        delta = float(a[column] - b[column])
                        rows.append(
                            dict(
                                arm=arm,
                                reference=ref,
                                part=phase,
                                station=point,
                                metric=column,
                                reference_value=float(b[column]),
                                candidate_value=float(a[column]),
                                difference=delta,
                                relative_difference=delta / float(b[column])
                                if b[column]
                                else np.nan,
                            )
                        )
    return pd.DataFrame(rows)


def compare_frame(expected, path, spec):
    actual = pd.read_csv(path)
    if expected.shape != actual.shape or list(expected) != list(actual):
        raise AssertionError("Table columns/shape differ: " + str(path))
    maxima = {}
    for column in expected:
        if pd.api.types.is_numeric_dtype(expected[column]):
            a, b = actual[column].to_numpy(float), expected[column].to_numpy(float)
            tolerance = (
                spec["coverage_tolerance"]
                if column.startswith("coverage_")
                else spec["score_atol_mm"]
            )
            np.testing.assert_allclose(a, b, atol=tolerance, rtol=0, equal_nan=True)
            valid = np.isfinite(b)
            maxima[column] = (
                float(abs(a[valid] - b[valid]).max()) if valid.any() else None
            )
        else:
            np.testing.assert_array_equal(
                actual[column].astype(str), expected[column].astype(str)
            )
    return dict(rows=len(actual), max_absolute_difference_by_column=maxima)


def check_saved_scores(tables, spec):
    metrics, aggregate, _ = tables
    result = dict(original_m0_e0=check_historical_scores(metrics, aggregate, spec))
    for name, frame in zip(
        ("metrics.csv", "aggregate.csv", "daily_predictions.csv"), tables
    ):
        selected = frame[frame.strategy.isin(("M0", "v1.1-e0", "BPLUS_GP_V1"))].copy()
        selected["strategy"] = selected.strategy.replace({"BPLUS_GP_V1": "BPLUS_GP"})
        result[name] = compare_frame(selected, ROOT / spec["old_gp_dir"] / name, spec)
    return result


def decide(metrics, spec):
    expected = {
        (s, p, j) for s in STRATEGIES for p in ("train", "prediction") for j in POINTS
    }
    if (
        len(metrics) != 40
        or set(map(tuple, metrics[["strategy", "part", "station"]].values)) != expected
    ):
        raise ValueError("All five strategies and complete point/windows are required")
    numeric = [c for c in metrics.select_dtypes(include="number") if c != "days"]
    probability = [c for c in numeric if c not in ("mae_mm", "rmse_mm")]
    if not metrics.loc[metrics.strategy == "M0", probability].isna().all().all():
        raise ValueError("M0 probability metrics must remain not applicable")
    if not np.isfinite(metrics.loc[metrics.strategy != "M0", numeric]).all().all():
        raise ValueError("Incomplete probability scores")
    if not np.isfinite(metrics[["mae_mm", "rmse_mm"]]).all().all():
        raise ValueError("Incomplete mean scores")
    if (metrics.loc[metrics.strategy != "M0", numeric] < 0).any().any():
        raise ValueError("Negative error/probability score")
    ix = metrics.set_index(["strategy", "part", "station"])
    avg = metrics.groupby(["strategy", "part"]).mean(numeric_only=True)
    eps = spec["strict_tolerance_mm"]
    gates = []

    def add(name, a, b, threshold, passed):
        gates.append(
            dict(
                name=name,
                candidate=float(a),
                reference=float(b),
                required_reduction=float(threshold),
                actual_reduction=float(b - a),
                passed=bool(passed),
            )
        )

    for metric in ("mae_mm", "rmse_mm"):
        a, b = avg.loc[("ADD", "train"), metric], avg.loc[("M0", "train"), metric]
        add("train_" + metric, a, b, eps, b - a > eps)
        for ref in ("M0", "TIME_ONLY"):
            a, b = (
                avg.loc[("ADD", "prediction"), metric],
                avg.loc[(ref, "prediction"), metric],
            )
            threshold = max(
                spec["material_mean_absolute_mm"], spec["material_mean_relative"] * b
            )
            add(
                "prediction_" + metric + "_vs_" + ref,
                a,
                b,
                threshold,
                b - a + eps >= threshold,
            )
    for metric in ("crps_mm", "interval_score_90_mm"):
        for ref in ("v1.1-e0", "TIME_ONLY"):
            a, b = (
                avg.loc[("ADD", "prediction"), metric],
                avg.loc[(ref, "prediction"), metric],
            )
            threshold = spec["material_probability_relative"] * b
            add(
                "prediction_" + metric + "_vs_" + ref,
                a,
                b,
                threshold,
                b - a >= threshold and b - a > eps,
            )
    for metric in ("mae_mm", "rmse_mm", "crps_mm", "interval_score_90_mm"):
        a, b = (
            avg.loc[("ADD", "prediction"), metric],
            avg.loc[("BPLUS_GP_V1", "prediction"), metric],
        )
        add("retain_gp_v1_" + metric, a, b, -eps, a <= b + eps)
    points = []
    for point in POINTS:
        row = dict(station=point, checks={})
        for phase, refs in (("train", ("M0",)), ("prediction", ("M0", "TIME_ONLY"))):
            for ref in refs:
                for metric in ("mae_mm", "rmse_mm"):
                    a, b = (
                        ix.loc[("ADD", phase, point), metric],
                        ix.loc[(ref, phase, point), metric],
                    )
                    row["checks"][phase + "_" + metric + "_vs_" + ref] = bool(
                        a <= b + eps
                    )
        for ref in ("v1.1-e0", "TIME_ONLY"):
            a, b = (
                ix.loc[("ADD", "prediction", point)],
                ix.loc[(ref, "prediction", point)],
            )
            for metric in ("crps_mm", "interval_score_90_mm"):
                row["checks"][metric + "_vs_" + ref] = bool(
                    a[metric] <= b[metric] + eps
                )
            row["checks"]["coverage_vs_" + ref] = bool(
                abs(a.coverage_90 - 0.9)
                <= abs(b.coverage_90 - 0.9)
                + spec["coverage_slack"]
                + spec["coverage_tolerance"]
            )
            row["checks"]["width_vs_" + ref] = bool(
                a.width_90_mm <= b.width_90_mm + eps
                or a.interval_score_90_mm < b.interval_score_90_mm - eps
            )
        row["passed"] = all(row["checks"].values())
        points.append(row)
    return dict(
        candidate="ADD",
        control="TIME_ONLY",
        effect_passed=all(g["passed"] for g in gates)
        and all(p["passed"] for p in points),
        aggregate_gates=gates,
        point_guards=points,
        next_action="stop; do not promote control, retry, add models or train final window",
    )
