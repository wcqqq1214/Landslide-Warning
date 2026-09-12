"""Regularized local dynamics with a learned heteroscedastic Gaussian scale."""

import json
from pathlib import Path
import shutil
import numpy as np
from scipy.linalg import solve
from scipy.optimize import minimize

from physics_guided.reference import ROOT, save_json, sha
from .data import array_sha, read_prefix, training_examples


def dynamic_features(x, experts):
    if experts.shape[2] != 8:
        raise ValueError("Ridge requires seven ordered experts and persistence")
    b, bt, d1, d3, d7, d14, d30, persist = np.moveaxis(experts, 2, 0)
    h = np.arange(1, experts.shape[1] + 1)[None, :, None]
    physics1 = b - persist - h * x[:, -1, 1][:, None, :]
    features = np.stack(
        [d1 - d3, d1 - d7, d7 - d14, d14 - d30, physics1, bt - d14], axis=-1
    )
    q = np.sqrt(np.mean(features[..., :4] ** 2, axis=-1))
    return features, d1, q


def scale_objective(logs, residual, q, floor):
    a2, b2 = np.exp(2 * np.asarray(logs))
    var = a2 + b2 * q * q + floor * floor
    loss = 0.5 * np.mean(np.log(var) + residual * residual / var)
    common = 0.5 * (1 / var - residual * residual / (var * var))
    gradient = np.array([np.mean(common * 2 * a2), np.mean(common * 2 * b2 * q * q)])
    return float(loss), gradient


class RidgeDistribution:
    family = "ridge_dynamic"

    def __init__(self, state):
        self.state = state
        for k in ("feature_scale", "target_scale", "beta", "scale_logs"):
            setattr(self, k, np.array(state[k], dtype=float))
        self.dimensions = int(state["dimensions"])

    def predict_raw(self, x, z, anchor, experts):
        features, base, q = dynamic_features(x, experts)
        H = features.shape[1]
        normalized = features[..., : self.dimensions] / self.feature_scale[None, :H]
        residual = np.einsum("nhpd,hpd->nhp", normalized, self.beta[:H], optimize=False)
        mu = base + residual * self.target_scale[None, :H]
        a, b = np.exp(self.scale_logs).T
        sigma = np.sqrt(
            (a[None, None, :] * self.target_scale[None, :H]) ** 2
            + (b[None, None, :] * q) ** 2
            + self.state["sigma_floor_mm"] ** 2
        )
        return mu, sigma

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text()))


def fit_distribution(data, alpha, arm, spec):
    experts = np.stack([data["baselines"][k] for k in spec["expert_names"]], axis=2)
    features, base, q = dynamic_features(data["x"], experts)
    D = 6 if arm == "FULL" else 4
    features = features[..., :D]
    sx = np.maximum(np.sqrt(np.mean(features**2, axis=0)), 1e-6)
    sy = np.maximum(
        np.sqrt(np.mean((data["y"] - base) ** 2, axis=0)),
        spec["probability"]["sigma_floor_mm"],
    )
    x = features / sx[None]
    target = (data["y"] - base) / sy[None]
    beta = np.zeros((features.shape[1], 4, D))
    N = len(features)
    max_normal_error = 0.0
    for h in range(features.shape[1]):
        for p in range(4):
            A = x[:, h, p]
            gram = np.einsum("ni,nj->ij", A, A, optimize=False) / N + float(
                alpha
            ) * np.eye(D)
            rhs = np.einsum("ni,n->i", A, target[:, h, p], optimize=False) / N
            coef = solve(gram, rhs, assume_a="pos")
            beta[h, p] = coef
            max_normal_error = max(
                max_normal_error,
                float(
                    np.max(abs(np.einsum("ij,j->i", gram, coef, optimize=False) - rhs))
                ),
            )
    fit_residual = target - np.einsum("nhpd,hpd->nhp", x, beta, optimize=False)
    cfg = spec["ridge"]
    scale_logs = []
    scale_records = []
    for p in range(4):
        residual = fit_residual[:, :, p]
        qn = q[:, :, p] / sy[None, :, p]
        floor = spec["probability"]["sigma_floor_mm"] / sy[None, :, p]
        op = minimize(
            scale_objective,
            np.array(cfg["scale_initial_logs"]),
            args=(residual, qn, floor),
            jac=True,
            method="L-BFGS-B",
            bounds=[cfg["scale_log_bounds"]] * 2,
            options=dict(
                maxiter=cfg["scale_maxiter"],
                maxfun=cfg["scale_maxfun"],
                ftol=cfg["scale_ftol"],
                gtol=cfg["scale_gtol"],
            ),
        )
        if not np.isfinite(op.x).all() or not np.isfinite(op.fun):
            raise ArithmeticError("Invalid conditional scale fit")
        scale_logs.append(op.x.tolist())
        scale_records.append(
            dict(
                point=p,
                success=bool(op.success),
                message=str(op.message),
                iterations=int(op.nit),
                function_calls=int(op.nfev),
                objective=float(op.fun),
                logs=op.x.tolist(),
            )
        )
    state = dict(
        family="ridge_dynamic",
        arm=arm,
        alpha=float(alpha),
        dimensions=D,
        feature_scale=sx.tolist(),
        target_scale=sy.tolist(),
        beta=beta.tolist(),
        scale_logs=scale_logs,
        scale_optimizer_records=scale_records,
        sigma_floor_mm=spec["probability"]["sigma_floor_mm"],
        training_samples=N,
        linear_solves=features.shape[1] * 4,
        max_normal_equation_residual=max_normal_error,
        input_feature_order=[
            "D1-D3",
            "D1-D7",
            "D7-D14",
            "D14-D30",
            "B_future_minus_B_v1",
            "B_future_minus_B_v14",
        ][:D],
    )
    model = RidgeDistribution(state)
    mean, sd = model.predict_raw(data["x"], data["z"], data["anchor"], experts)
    if not np.isfinite(mean).all() or not np.isfinite(sd).all():
        raise ArithmeticError("Nonfinite ridge output")
    return model


def training_data(labels, pool, spec, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    data = training_examples(
        labels, pool, spec["horizons"], spec["history_days"], spec["extra_baselines"]
    )
    np.savez_compressed(
        out / "training_queries.npz",
        origins=data["origins"],
        teachers=data["teachers"],
        target_last=data["origins"] + spec["horizons"] - 1,
    )
    save_json(
        out / "data_contract.json",
        dict(
            label_rows=len(labels),
            label_sha256=array_sha(labels),
            samples=len(data["origins"]),
            max_target_index=int((data["origins"] + spec["horizons"] - 1).max()),
            model_family="ridge_dynamic",
        ),
    )
    scales = {
        k: np.maximum(0.01, np.sqrt(np.mean((data["y"] - v) ** 2, axis=0)))
        for k, v in data["baselines"].items()
    }
    save_json(out / "baseline_scales.json", {k: v.tolist() for k, v in scales.items()})
    return data, scales


def train_group(data, spec, out, arm, alpha, recorder):
    model = fit_distribution(data, alpha, arm, spec)
    path = Path(out) / f"ridge_{arm}_a{alpha:g}.json"
    save_json(path, model.state)
    recorder.event(
        "ridge_model_locked",
        arm=arm,
        alpha=float(alpha),
        path=str(path.relative_to(ROOT)),
        sha256=sha(path),
        linear_solves=model.state["linear_solves"],
        scale_calls=sum(
            r["function_calls"] for r in model.state["scale_optimizer_records"]
        ),
    )
    return [model]


def develop(pool, spec, out, recorder):
    from .run import forecast_phase
    from .scoring import gate

    if spec.get("reuse_development"):
        return recalibrate_development(pool, spec, out, recorder)
    prefix = spec.get("model_prefix", "C3_RIDGE")

    inner_start, inner_end = spec["stages"]["inner"]
    start, end = spec["stages"]["development"]
    labels = read_prefix(ROOT / spec["data"], inner_start)
    recorder.event("training_prefix_read", rows=len(labels))
    data, scales = training_data(labels, pool, spec, out / "inner_training")
    groups = {}
    for arm in spec["ridge"]["arms"]:
        for alpha in spec["ridge"]["alphas"]:
            groups[f"{prefix}_{arm}_a{alpha:g}"] = train_group(
                data, spec, out / "inner_training", arm, alpha, recorder
            )
    _, summary = forecast_phase(
        pool,
        spec,
        inner_start,
        inner_end,
        groups,
        None,
        scales,
        out / "inner",
        recorder,
    )
    s = summary[summary.horizon == spec["primary_horizon"]].set_index("model")
    reference = s.loc["B_ANCHOR"]
    selected = {}
    selection_scores = {}
    for arm in spec["ridge"]["arms"]:
        scores = {
            a: float(
                s.loc[f"{prefix}_{arm}_a{a:g}", "rmse"] / reference.rmse
                + s.loc[f"{prefix}_{arm}_a{a:g}", "crps"] / reference.crps
            )
            for a in spec["ridge"]["alphas"]
        }
        selected[arm] = min(scores, key=lambda a: (scores[a], -a))
        selection_scores[arm] = scores
    save_json(
        out / "internal_selection.json",
        dict(selected_alpha=selected, scores=selection_scores),
    )
    recorder.event(
        "ridge_selection_locked", selected_alpha=selected, scores=selection_scores
    )
    labels = read_prefix(ROOT / spec["data"], start)
    recorder.event("training_prefix_read", rows=len(labels))
    data, scales = training_data(labels, pool, spec, out / "development_training")
    groups = {
        f"{prefix}_{arm}": train_group(
            data, spec, out / "development_training", arm, selected[arm], recorder
        )
        for arm in spec["ridge"]["arms"]
    }
    metrics, summary = forecast_phase(
        pool, spec, start, end, groups, None, scales, out / "development", recorder
    )
    s = summary[summary.horizon == spec["primary_horizon"]].set_index("model")
    simple = min(
        ("B_TREND14", "PERSIST", "DRIFT14", *spec["extra_baselines"]),
        key=lambda name: s.loc[name, "rmse"],
    )
    decision = gate(metrics, summary, spec["candidate"], simple, spec)
    decision.update(selected_alpha=selected, selected_updates=0, neural_updates=0)
    save_json(out / "decision.json", decision)
    recorder.event("development_decision", **decision)
    return decision


def recalibrate_development(pool, spec, out, recorder):
    """Reissue the frozen C3 raw forecasts without fitting or selecting again."""
    from .run import forecast_phase
    from .scoring import gate

    reuse = spec["reuse_development"]
    prior = ROOT / reuse["path"]
    manifest = prior / "artifact_manifest.json"
    if sha(manifest) != reuse["artifact_manifest_sha256"]:
        raise ValueError("Reused development manifest changed")
    for name, expected in json.loads(manifest.read_text())["files"].items():
        if sha(prior / name) != expected:
            raise ValueError("Reused development artifact changed: " + name)
    selected = json.loads((prior / "internal_selection.json").read_text())[
        "selected_alpha"
    ]
    if selected != reuse["selected_alpha"]:
        raise ValueError("Reused ridge alpha differs from the frozen choice")
    shutil.copytree(prior / "development_training", out / "development_training")
    shutil.copyfile(prior / "internal_selection.json", out / "internal_selection.json")
    train_dir = out / "development_training"
    scales = {
        k: np.array(v)
        for k, v in json.loads((train_dir / "baseline_scales.json").read_text()).items()
    }
    prefix = spec["model_prefix"]
    groups = {
        f"{prefix}_{arm}": [
            RidgeDistribution.load(train_dir / f"ridge_{arm}_a{selected[arm]:g}.json")
        ]
        for arm in spec["ridge"]["arms"]
    }
    recorder.event(
        "frozen_development_models_reused",
        source=reuse,
        selected_alpha=selected,
        new_model_fits=0,
    )
    start, end = spec["stages"]["development"]
    metrics, summary = forecast_phase(
        pool, spec, start, end, groups, None, scales, out / "development", recorder
    )
    checks = {}
    for path in (out / "development").glob("*.npz"):
        old_name = path.name.replace(prefix, reuse["model_prefix"])
        with np.load(path) as new, np.load(prior / "development" / old_name) as old:
            for key in ("origins", "teacher_prefixes", "mean", "raw_sigma"):
                np.testing.assert_array_equal(new[key], old[key])
        checks[path.stem] = dict(
            mean_max_difference_mm=0.0, raw_sigma_max_difference_mm=0.0
        )
    save_json(out / "raw_forecast_identity.json", dict(source=reuse, models=checks))
    s = summary[summary.horizon == spec["primary_horizon"]].set_index("model")
    simple = min(
        ("B_TREND14", "PERSIST", "DRIFT14", *spec["extra_baselines"]),
        key=lambda name: s.loc[name, "rmse"],
    )
    decision = gate(metrics, summary, spec["candidate"], simple, spec)
    decision.update(
        selected_alpha=selected,
        selected_updates=0,
        neural_updates=0,
        new_model_fits=0,
        frozen_development_reused=reuse["path"],
    )
    save_json(out / "decision.json", decision)
    recorder.event("development_decision", **decision)
    return decision


def transfer(pool, spec, out, recorder, development):
    from .run import forecast_phase, utc
    from .scoring import gate

    prior = Path(development).resolve()
    decision = json.loads((prior / "decision.json").read_text())
    if not decision["passed"] or decision["candidate"] != spec["candidate"]:
        raise ValueError("Unqualified ridge candidate cannot enter transfer")
    selected = decision["selected_alpha"]
    save_json(
        out / "transfer_lock.json",
        dict(
            development_path=str(prior.relative_to(ROOT)),
            decision_sha256=sha(prior / "decision.json"),
            candidate=spec["candidate"],
            selected_alpha=selected,
            simple_baseline=decision["simple_baseline"],
            locked_utc=utc(),
        ),
    )
    start, end = spec["stages"]["transfer"]
    labels = read_prefix(ROOT / spec["data"], start)
    recorder.event("training_prefix_read", rows=len(labels))
    data, scales = training_data(labels, pool, spec, out / "transfer_training")
    prefix = spec.get("model_prefix", "C3_RIDGE")
    groups = {
        f"{prefix}_{arm}": train_group(
            data, spec, out / "transfer_training", arm, selected[arm], recorder
        )
        for arm in spec["ridge"]["arms"]
    }
    metrics, summary = forecast_phase(
        pool, spec, start, end, groups, None, scales, out / "transfer", recorder
    )
    result = gate(
        metrics, summary, spec["candidate"], decision["simple_baseline"], spec
    )
    result.update(selected_alpha=selected, selected_updates=0, neural_updates=0)
    save_json(out / "decision.json", result)
    recorder.event("transfer_decision", **result)
    return result
