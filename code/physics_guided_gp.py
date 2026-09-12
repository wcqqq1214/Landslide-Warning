"""Frozen B+ residual GPs; no physical solver or final-window label reader."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import scipy
from scipy.linalg import solve_triangular
from scipy.optimize import minimize
from scipy.spatial.distance import cdist
import sklearn
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel

from physics_guided.probability import crps, interval_score, summarize

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/ootang_bplus_gp.v1.json"
CONFIG_SHA = "f49fb4414f18598794ab706341df3af5644c53b57aa71ffa98ab2a724b9055c8"
POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")
STRATEGIES = ("M0", "v1.1-e0", "BPLUS_GP")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def read_npz(path):
    with np.load(path, allow_pickle=False) as data:
        return {key: data[key] for key in data.files}


def check_sources(mapping):
    for name, digest in mapping.items():
        if sha(ROOT / name) != digest:
            raise ValueError("Frozen source differs: " + name)


def specification():
    if sha(CONFIG) != CONFIG_SHA:
        raise ValueError("Approved configuration changed")
    spec = json.loads(CONFIG.read_text())
    check_sources(spec["source_sha256"])
    actual = dict(
        numpy=np.__version__, scipy=scipy.__version__, sklearn=sklearn.__version__
    )
    if actual != spec["versions"]:
        raise ValueError("Use the frozen dependency versions")
    return spec


def read_labels(path, rows):
    if rows not in (792, 1168):
        raise ValueError("Only registered fit/development prefixes may be read")
    frame = pd.read_csv(
        path, nrows=rows, usecols=["Date", *[p + "/mm" for p in POINTS]]
    )
    dates = pd.DatetimeIndex(pd.to_datetime(frame.Date))
    if not dates.equals(pd.date_range("2016-07-01", periods=rows)):
        raise ValueError("Observation date prefix differs")
    labels = frame[[p + "/mm" for p in POINTS]].to_numpy(np.float64)
    if labels.shape != (rows, 4) or not np.isfinite(labels).all():
        raise ValueError("Incomplete finite observation prefix")
    return labels


def load_reference(spec):
    saved = read_npz(ROOT / spec["reference"])
    old = read_npz(ROOT / spec["probability_reference"])
    params = json.loads((ROOT / spec["parameter_source"]).read_text())
    package = json.loads((ROOT / spec["physics_package"]).read_text())
    selection = json.loads((ROOT / spec["selection"]).read_text())["routes"]["M1"]
    index = json.loads((ROOT / spec["reference_manifest"]).read_text())["files"]
    record = index[Path(spec["reference"]).name]
    if sha(ROOT / spec["reference"]) != record["sha256"]:
        raise ValueError("Reference array differs from its original sealed manifest")
    for obj in (params, package):
        if (
            obj["physics_version"] != "prefix_792_v1_1"
            or obj["fit_end"] != "2018-08-31"
        ):
            raise ValueError("Original 792-day B+ parameters required")
        np.testing.assert_array_equal(saved["theta"], obj["theta"])
    np.testing.assert_array_equal(saved["y0"], package["y0"])
    if (
        saved["theta"].shape != (54,)
        or selection["e_mu"] != 0
        or selection["e_sigma"] != 0
    ):
        raise ValueError("Frozen physical parameter/selected e0 identity differs")
    if list(spec["points"]) != list(POINTS):
        raise ValueError(
            "Point order differs from the original physical data interface"
        )
    dates = pd.date_range("2016-07-01", periods=1168).strftime("%Y-%m-%d").to_numpy()
    np.testing.assert_array_equal(saved["dates"], dates)
    shapes = dict(
        mean=(1168, 4),
        moisture=(1168, 4),
        rain_head=(1168, 4),
        reservoir_head=(1168,),
        forcing=(1168, 2),
    )
    for key, shape in shapes.items():
        if saved[key].shape != shape or not np.isfinite(saved[key]).all():
            raise ValueError("Invalid reference field: " + key)
    if old["means"].shape != (3, 1168, 4) or old["sigmas"].shape != (3, 1168, 4):
        raise ValueError("The complete three-seed historical distribution is required")
    if (
        not np.isfinite(old["means"][:, 30:]).all()
        or not np.isfinite(old["sigmas"][:, 30:]).all()
    ):
        raise ValueError("Incomplete historical valid dates")
    if (old["sigmas"][:, 30:] <= 0).any():
        raise ValueError("Invalid historical scale")
    difference = float(np.max(abs(old["means"][:, 30:] - saved["mean"][None, 30:])))
    if difference > spec["reference_mean_atol_mm"]:
        raise ValueError("Saved reference is not the original B+ e0 mean")
    # Read only the known driver columns; this never parses future displacement.
    frame = pd.read_csv(
        ROOT / spec["data"], nrows=1168, usecols=["Date", "Rainfall/mm", "RWL/m"]
    )
    np.testing.assert_array_equal(
        pd.to_datetime(frame.Date).dt.strftime("%Y-%m-%d"), dates
    )
    np.testing.assert_array_equal(
        frame[["Rainfall/mm", "RWL/m"]].to_numpy(float), saved["forcing"]
    )
    audit = dict(
        dates_start=str(dates[0]),
        fit_end=str(dates[791]),
        prediction_start=str(dates[792]),
        dates_end=str(dates[-1]),
        fit_days=792,
        gp_training_days=762,
        prediction_days=376,
        point_order=list(POINTS),
        theta_values=54,
        physics_version=params["physics_version"],
        parameter_sha256=package["parameter_sha256"],
        reference_sha256=sha(ROOT / spec["reference"]),
        max_reference_to_e0_mean_error_mm=difference,
        physical_solver_calls=0,
        displacement_label_rows_read=0,
        original_array_shapes={k: list(v) for k, v in shapes.items()},
    )
    return saved, old, audit


def raw_features(saved):
    n = len(saved["mean"])
    rwl = saved["forcing"][:, 1]
    values = np.column_stack(
        (
            np.arange(n, dtype=float),
            saved["moisture"].mean(axis=1),
            saved["rain_head"].mean(axis=1),
            saved["reservoir_head"] - rwl,
            np.diff(rwl, prepend=rwl[0]),
        )
    )
    if values.shape != (n, 5) or not np.isfinite(values).all():
        raise ValueError("Five finite physical summary features required")
    return values


@dataclass
class Inputs:
    raw: np.ndarray
    x: np.ndarray
    targets: np.ndarray
    normalizers: dict


def prepare_inputs(raw, base, labels, spec):
    h, n, start = spec["fit_days"], spec["end_days"], spec["warmup_days"]
    if labels.shape != (h, 4) or raw.shape != (n, 5) or base.shape != (n, 4):
        raise ValueError("The exact training-label prefix is required")
    if not all(np.isfinite(v).all() for v in (raw, base, labels)):
        raise ValueError("Nonfinite input; no filling")
    train = raw[start:h]
    center, deviation = train.mean(axis=0), train.std(axis=0, ddof=0)
    constant = deviation <= spec["x_std_floor"]
    scale = np.where(constant, 1.0, deviation)
    residual = labels[start:h] - base[start:h]
    rms = np.sqrt(np.mean(residual**2, axis=0))
    denominator = np.maximum(rms, spec["residual_rms_floor_mm"])
    normalizers = dict(
        feature_mean=center.tolist(),
        feature_std=deviation.tolist(),
        feature_denominator=scale.tolist(),
        constant_features=constant.tolist(),
        residual_rms_mm=rms.tolist(),
        residual_denominator_mm=denominator.tolist(),
        residual_floor_used=(rms < spec["residual_rms_floor_mm"]).tolist(),
        fit_range=[start, h],
        residual_centering=False,
    )
    return Inputs(
        raw.copy(), (raw - center) / scale, residual / denominator, normalizers
    )


def kernel(spec):
    value = spec["kernel"]
    return ConstantKernel(
        value["amplitude"], tuple(value["amplitude_bounds"])
    ) * Matern(
        value["length_scales"], tuple(value["length_bounds"]), nu=value["nu"]
    ) + WhiteKernel(value["noise_variance"], tuple(value["noise_bounds"]))


class BoundedOptimizer:
    """Hard-count every objective request, including line-search evaluations."""

    def __init__(self, spec, check=lambda: None, record=lambda kind, data: None):
        self.spec, self.check, self.record = spec, check, record
        self.calls, self.iterations, self.invocations = 0, 0, 0
        self.result = None

    def __call__(self, objective, initial, bounds):
        if self.invocations:
            raise RuntimeError("Optimizer restarts are forbidden")
        self.invocations += 1
        settings = self.spec["optimizer"]

        def value(theta):
            self.check()
            if self.calls >= settings["max_evaluations"]:
                raise RuntimeError("Point objective-call limit reached; no retry")
            self.calls += 1
            self.record("objective_requested", dict(evaluation=self.calls))
            loss, gradient = objective(theta, eval_gradient=True)
            if not np.isfinite(loss) or not np.isfinite(gradient).all():
                raise ArithmeticError("Nonfinite likelihood or gradient")
            self.record(
                "objective",
                dict(
                    evaluation=self.calls,
                    loss=float(loss),
                    gradient_norm=float(np.linalg.norm(gradient)),
                    log_theta=theta.tolist(),
                ),
            )
            return loss, gradient

        def callback(theta):
            self.check()
            self.iterations += 1
            if self.iterations > settings["maxiter"]:
                raise RuntimeError("Point iteration limit reached")
            self.record(
                "iteration", dict(iteration=self.iterations, log_theta=theta.tolist())
            )

        result = minimize(
            value,
            initial,
            method="L-BFGS-B",
            jac=True,
            bounds=bounds,
            callback=callback,
            options={
                k: settings[k] for k in ("maxiter", "ftol", "gtol", "maxls", "maxcor")
            }
            | {"maxfun": settings["max_evaluations"]},
        )
        self.result = dict(
            success=bool(result.success),
            status=int(result.status),
            message=str(result.message),
            iterations=int(result.nit),
            evaluations=int(result.nfev),
            loss=float(result.fun),
            log_theta=result.x.tolist(),
        )
        self.record("optimizer_end", self.result)
        if result.nfev != self.calls or result.nit != self.iterations:
            raise AssertionError("Optimizer accounting differs")
        if not result.success:
            raise RuntimeError("Point optimization incomplete: " + str(result.message))
        return result.x, result.fun


def new_gp(spec, optimizer):
    return GaussianProcessRegressor(
        kernel=kernel(spec),
        alpha=spec["jitter"],
        optimizer=optimizer,
        n_restarts_optimizer=0,
        normalize_y=False,
        copy_X_train=True,
        random_state=0,
    )


def predict(gp, x, base, residual_scale, spec):
    mu, std = gp.predict(x, return_std=True)
    cross = gp.kernel_.k1(x, gp.X_train_)
    v = solve_triangular(gp.L_, cross.T, lower=True, check_finite=False)
    raw_latent = gp.kernel_.k1.diag(x) - np.einsum("ij,ij->j", v, v)
    amplitude = float(gp.kernel_.k1.k1.constant_value)
    noise = float(gp.kernel_.k2.noise_level)
    bound = spec["negative_variance_factor"] * max(1.0, amplitude)
    if raw_latent.min() < -bound:
        raise ArithmeticError("Latent variance is below the fixed roundoff bound")
    latent = np.maximum(raw_latent, 0)
    np.testing.assert_allclose(std**2, raw_latent + noise, rtol=1e-10, atol=bound)
    result = dict(
        mean=base + mu * residual_scale,
        residual_mean=mu * residual_scale,
        latent_variance=latent * residual_scale**2,
        observation_variance=std**2 * residual_scale**2,
        noise_variance=np.full(len(x), noise * residual_scale**2),
    )
    if (
        not all(np.isfinite(v).all() for v in result.values())
        or (result["observation_variance"] <= 0).any()
    ):
        raise ArithmeticError("Invalid complete observation prediction")
    audit = dict(
        raw_latent_min=float(raw_latent.min()),
        normalized_negative_bound=bound,
        rounded_negative_values=int(sum(raw_latent < 0)),
        amplitude=amplitude,
        length_scales=np.asarray(gp.kernel_.k1.k2.length_scale).tolist(),
        noise_variance_normalized=noise,
    )
    return result, audit


def independent_posterior(x_train, targets, x, amplitude, lengths, noise, jitter):
    """Separate Matérn formula and LU solves; no sklearn kernel/predict/fit."""

    def covariance(a, b):
        distance = np.sqrt(cdist(a / lengths, b / lengths, metric="sqeuclidean"))
        distance *= np.sqrt(3.0)
        return amplitude * (1 + distance) * np.exp(-distance)

    train = covariance(x_train, x_train) + (noise + jitter) * np.eye(len(x_train))
    cross = covariance(x, x_train)
    mu = cross @ np.linalg.solve(train, targets)
    latent = amplitude - np.einsum("ij,ji->i", cross, np.linalg.solve(train, cross.T))
    return mu, latent, latent + noise


def distributions(saved, old, prediction):
    return {
        "M0": (saved["mean"][None], None),
        "v1.1-e0": (old["means"], old["sigmas"]),
        "BPLUS_GP": (
            prediction["mean"][None],
            np.sqrt(prediction["observation_variance"])[None],
        ),
    }


def score_distributions(saved, old, prediction, labels):
    if labels.shape != (1168, 4):
        raise ValueError("Only the complete registered development task may be scored")
    metrics, aggregate, daily = [], [], []
    for name, (means, sigmas) in distributions(saved, old, prediction).items():
        mu = means[:, 30:].mean(axis=0)
        summary = summarize(means[:, 30:], sigmas[:, 30:]) if sigmas is not None else {}
        cps = (
            crps(means[:, 30:], sigmas[:, 30:], labels[30:])
            if sigmas is not None
            else None
        )
        for phase, start, end in (("train", 30, 792), ("prediction", 792, 1168)):
            loc = slice(start - 30, end - 30)
            error = mu[loc] - labels[start:end]
            phase_rows = []
            for j, station in enumerate(POINTS):
                row = dict(
                    strategy=name,
                    part=phase,
                    station=station,
                    days=end - start,
                    mae_mm=float(np.mean(abs(error[:, j]))),
                    rmse_mm=float(np.sqrt(np.mean(error[:, j] ** 2))),
                    crps_mm=float(cps[loc, j].mean()) if cps is not None else np.nan,
                )
                for level in (80, 90, 95):
                    if sigmas is None:
                        row.update(
                            {
                                f"coverage_{level}": np.nan,
                                f"width_{level}_mm": np.nan,
                                f"interval_score_{level}_mm": np.nan,
                            }
                        )
                    else:
                        lower, upper = (
                            summary[f"{edge}_{level}"][loc, j]
                            for edge in ("lower", "upper")
                        )
                        y = labels[start:end, j]
                        row.update(
                            {
                                f"coverage_{level}": float(
                                    np.mean((y >= lower) & (y <= upper))
                                ),
                                f"width_{level}_mm": float(np.mean(upper - lower)),
                                f"interval_score_{level}_mm": float(
                                    interval_score(y, lower, upper, level).mean()
                                ),
                            }
                        )
                phase_rows.append(row)
            metrics.extend(phase_rows)
            values = (
                pd.DataFrame(phase_rows)
                .drop(columns="days")
                .mean(numeric_only=True)
                .to_dict()
            )
            common = dict(
                strategy=name,
                part=phase,
                days=end - start,
                point_days=(end - start) * 4,
            )
            aggregate.append(dict(**common, aggregation="point_mean", **values))
            values["rmse_mm"] = float(np.sqrt(np.mean(error**2)))
            aggregate.append(dict(**common, aggregation="pooled", **values))
        for j, station in enumerate(POINTS):
            frame = pd.DataFrame(
                dict(
                    strategy=name,
                    station=station,
                    day=np.arange(30, 1168),
                    date=saved["dates"][30:],
                    part=np.where(np.arange(30, 1168) < 792, "train", "prediction"),
                    observed_mm=labels[30:, j],
                    mean_mm=mu[:, j],
                    error_mm=mu[:, j] - labels[30:, j],
                    crps_mm=cps[:, j] if cps is not None else np.nan,
                )
            )
            for level in (80, 90, 95):
                for edge in ("lower", "upper"):
                    frame[f"{edge}_{level}_mm"] = (
                        summary[f"{edge}_{level}"][:, j] if summary else np.nan
                    )
            daily.append(frame)
    return (
        pd.DataFrame(metrics),
        pd.DataFrame(aggregate),
        pd.concat(daily, ignore_index=True),
    )


def decide(metrics, spec):
    keys = ["strategy", "part", "station"]
    expected = {
        (s, p, j) for s in STRATEGIES for p in ("train", "prediction") for j in POINTS
    }
    if len(metrics) != 24 or set(map(tuple, metrics[keys].values)) != expected:
        raise ValueError(
            "All three strategies, two phases and four points are required"
        )
    indexed = metrics.set_index(keys).sort_index()
    for name in STRATEGIES:
        block = indexed.loc[name]
        if not np.isfinite(block[["mae_mm", "rmse_mm"]]).all().all():
            raise ValueError("Invalid mean scores")
        if (
            name != "M0"
            and not np.isfinite(block.select_dtypes(include="number")).all().all()
        ):
            raise ValueError("Invalid probability scores")
        if (
            name == "M0"
            and not block[["crps_mm", "coverage_90", "interval_score_90_mm"]]
            .isna()
            .all()
            .all()
        ):
            raise ValueError("M0 probability scores must remain not applicable")
    tol = spec["strict_tolerance_mm"]
    average = metrics.groupby(["strategy", "part"]).mean(numeric_only=True)
    gates, points = {}, []
    for phase in ("train", "prediction"):
        for metric in ("mae_mm", "rmse_mm"):
            gates[f"{phase}_{metric}_improved"] = bool(
                average.loc[("BPLUS_GP", phase), metric]
                < average.loc[("M0", phase), metric] - tol
            )
    for metric in ("crps_mm", "interval_score_90_mm"):
        gates[f"prediction_{metric}_improved"] = bool(
            average.loc[("BPLUS_GP", "prediction"), metric]
            < average.loc[("v1.1-e0", "prediction"), metric] - tol
        )
    for j in POINTS:
        row = dict(station=j)
        for phase in ("train", "prediction"):
            a, b = indexed.loc[("BPLUS_GP", phase, j)], indexed.loc[("M0", phase, j)]
            for metric in ("mae_mm", "rmse_mm"):
                delta = float(a[metric] - b[metric])
                row[f"{phase}_{metric}_difference"] = delta
                row[f"{phase}_{metric}_relative_gain"] = (
                    -delta / float(b[metric]) if b[metric] else None
                )
                row[f"{phase}_{metric}_guard"] = bool(delta <= tol)
        a, b = (
            indexed.loc[("BPLUS_GP", "prediction", j)],
            indexed.loc[("v1.1-e0", "prediction", j)],
        )
        for metric in ("crps_mm", "interval_score_90_mm"):
            delta = float(a[metric] - b[metric])
            row[metric + "_difference"] = delta
            row[metric + "_relative_gain"] = (
                -delta / float(b[metric]) if b[metric] else None
            )
            row[metric + "_guard"] = bool(delta <= tol)
        row["coverage_guard"] = bool(
            abs(a.coverage_90 - 0.9)
            <= abs(b.coverage_90 - 0.9)
            + spec["coverage_slack"]
            + spec["coverage_tolerance"]
        )
        row["width_guard"] = bool(
            a.width_90_mm <= b.width_90_mm + tol
            or a.interval_score_90_mm < b.interval_score_90_mm - tol
        )
        row["passed"] = all(v for k, v in row.items() if k.endswith("_guard"))
        points.append(row)
    gates["all_point_guards"] = all(r["passed"] for r in points)
    return dict(
        candidate="BPLUS_GP",
        effect_passed=all(gates.values()),
        gates=gates,
        points=points,
        next_action="stop; no automatic extension or final-window training",
    )


def check_historical_scores(metrics, aggregate, spec):
    historical = pd.read_csv(ROOT / spec["historical_metrics"])
    historical = historical[historical.model.isin(("M0", "v1.1-e0"))]
    maximum, checked = 0.0, 0
    for row in historical.itertuples():
        column = (
            row.metric if row.metric.startswith("coverage_") else row.metric + "_mm"
        )
        if row.station in POINTS:
            source = metrics[
                (metrics.strategy == row.model)
                & (metrics.part == row.phase)
                & (metrics.station == row.station)
            ]
        else:
            source = aggregate[
                (aggregate.strategy == row.model)
                & (aggregate.part == row.phase)
                & (aggregate.aggregation == row.station)
            ]
        if len(source) != 1:
            raise ValueError("Historical score identity differs")
        value = float(source.iloc[0][column])
        delta = abs(value - row.value)
        tolerance = (
            spec["coverage_tolerance"]
            if column.startswith("coverage_")
            else spec["score_atol_mm"]
        )
        if not np.isfinite(value) or delta > tolerance:
            raise AssertionError(
                f"Historical score differs: {row.model}/{row.phase}/{row.station}/{column}: {delta}"
            )
        maximum = max(maximum, delta)
        checked += 1
    if checked != 144:
        raise AssertionError(
            f"Expected the complete historical M0/e0 scores, got {checked}"
        )
    return dict(checked_scores=checked, maximum_absolute_difference=maximum)
