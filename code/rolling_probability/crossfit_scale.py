"""C5: learn only the scale from chronological, held-out mean errors."""

from copy import deepcopy
import json
import shutil

import numpy as np
from scipy.optimize import minimize

from physics_guided.reference import ROOT, save_json, sha
from .data import read_prefix, training_examples
from .ridge import (
    RidgeDistribution,
    dynamic_features,
    scale_objective,
    train_group,
    training_data,
)


def subset(data, mask):
    return {
        k: (
            {name: a[mask] for name, a in v.items()} if isinstance(v, dict) else v[mask]
        )
        for k, v in data.items()
    }


def historical_queries(labels, pool, spec, fold_start, fold_end):
    if not 0 < fold_start < fold_end <= len(labels):
        raise ValueError("Historical fold crosses the available label prefix")
    data = training_examples(
        labels[:fold_end],
        pool,
        spec["horizons"],
        spec["history_days"],
        spec["extra_baselines"],
    )
    keep = (data["origins"] >= fold_start) & (
        data["origins"] + spec["horizons"] <= fold_end
    )
    selected = subset(data, keep)
    if (
        not len(selected["origins"])
        or (selected["teachers"] > selected["origins"]).any()
    ):
        raise ValueError("Invalid historical query boundaries")
    return selected


def refit_scale(mean_model, errors, q, spec):
    state = deepcopy(mean_model.state)
    sy = mean_model.target_scale
    if errors.shape != q.shape or errors.shape[1:] != sy.shape:
        raise ValueError("Scale targets have the wrong shape")
    cfg = spec["ridge"]
    logs, records = [], []
    for p in range(4):
        residual = errors[:, :, p] / sy[None, :, p]
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
            raise ArithmeticError("Nonfinite historical scale optimization")
        logs.append(op.x.tolist())
        records.append(
            dict(
                point=p,
                success=bool(op.success),
                message=str(op.message),
                iterations=int(op.nit),
                function_calls=int(op.nfev),
                objective=float(op.fun),
                logs=op.x.tolist(),
                gradient=op.jac.tolist(),
            )
        )
    state.update(
        scale_logs=logs,
        scale_optimizer_records=records,
        scale_training_mode="rolling_crossfit",
        scale_training_samples=len(errors),
    )
    for key in spec["crossfit"]["mean_fields_frozen"]:
        if state[key] != mean_model.state[key]:
            raise ValueError("Frozen mean field changed: " + key)
    return RidgeDistribution(state)


def copy_selected_training(source, target, arms, alpha):
    target.mkdir(parents=True)
    names = ["training_queries.npz", "data_contract.json", "baseline_scales.json"]
    names += [f"ridge_{arm}_a{alpha:g}.json" for arm in arms]
    for name in names:
        shutil.copyfile(source / name, target / name)


def develop(pool, spec, out, recorder):
    from .run import forecast_phase
    from .scoring import gate

    prior = ROOT / spec["reuse_development"]["path"]
    manifest_path = prior / "artifact_manifest.json"
    if sha(manifest_path) != spec["reuse_development"]["artifact_manifest_sha256"]:
        raise ValueError("Frozen C3 manifest changed")
    for name, expected in json.loads(manifest_path.read_text())["files"].items():
        if sha(prior / name) != expected:
            raise ValueError("Frozen C3 artifact changed: " + name)
    alpha = spec["crossfit"]["alpha"]
    arms = spec["ridge"]["arms"]
    selected = {arm: alpha for arm in arms}
    save_json(
        out / "internal_selection.json",
        dict(
            selected_alpha=selected,
            new_hyperparameter_selection=False,
            source=spec["reuse_development"],
        ),
    )
    folds = spec["crossfit"]["folds"]
    fold_models = {}
    new_auxiliary_fits = 0
    for prefix in sorted({start for start, _ in folds}):
        train_dir = out / "fold_training" / str(prefix)
        if prefix in spec["crossfit"]["reuse_prefixes"]:
            copy_selected_training(prior / "inner_training", train_dir, arms, alpha)
            fold_models[prefix] = {
                arm: RidgeDistribution.load(train_dir / f"ridge_{arm}_a{alpha:g}.json")
                for arm in arms
            }
            recorder.event("auxiliary_mean_reused", prefix=prefix, new_fits=0)
        else:
            labels = read_prefix(ROOT / spec["data"], prefix)
            recorder.event("auxiliary_training_prefix_read", rows=prefix)
            data, _ = training_data(labels, pool, spec, train_dir)
            fold_models[prefix] = {
                arm: train_group(data, spec, train_dir, arm, alpha, recorder)[0]
                for arm in arms
            }
            new_auxiliary_fits += len(arms)
    decisions = {}
    for phase in ("inner", "development"):
        start, end = spec["stages"][phase]
        labels = read_prefix(ROOT / spec["data"], start)
        recorder.event("scale_training_prefix_read", phase=phase, rows=start)
        source = prior / (
            "inner_training" if phase == "inner" else "development_training"
        )
        train_dir = out / f"{phase}_training"
        copy_selected_training(source, train_dir, arms, alpha)
        blocks = []
        for a, b in folds:
            if b > start:
                continue
            data = historical_queries(labels, pool, spec, a, b)
            experts = np.stack(
                [data["baselines"][k] for k in spec["expert_names"]], axis=2
            )
            _, _, q = dynamic_features(data["x"], experts)
            block = dict(
                origins=data["origins"],
                teacher_prefixes=data["teachers"],
                fit_prefix=np.full(len(q), a),
                y=data["y"],
                q=q,
            )
            for arm in arms:
                mu, _ = fold_models[a][arm].predict_raw(
                    data["x"], data["z"], data["anchor"], experts
                )
                block[f"mean_{arm}"] = mu
                block[f"error_{arm}"] = data["y"] - mu
            blocks.append(block)
        history = {k: np.concatenate([block[k] for block in blocks]) for k in blocks[0]}
        if (history["fit_prefix"] > history["origins"]).any():
            raise ValueError("Historical mean fitted beyond the forecast origin")
        np.savez_compressed(train_dir / "oof_predictions.npz", **history)
        save_json(
            train_dir / "oof_contract.json",
            dict(
                phase=phase,
                label_rows=start,
                samples=len(history["origins"]),
                max_target_index=int(history["origins"].max() + spec["horizons"] - 1),
                fixed_alpha=alpha,
                nested_hyperparameter_validation=False,
                files={"oof_predictions.npz": sha(train_dir / "oof_predictions.npz")},
            ),
        )
        recorder.event(
            "historical_scale_queries_locked",
            phase=phase,
            queries=len(history["origins"]),
            last_target_index=int(history["origins"].max() + 29),
            sha256=sha(train_dir / "oof_predictions.npz"),
        )
        suffix = f"_a{alpha:g}" if phase == "inner" else ""
        groups = {}
        for arm in arms:
            model_path = train_dir / f"ridge_{arm}_a{alpha:g}.json"
            original = RidgeDistribution.load(model_path)
            shutil.copyfile(model_path, train_dir / f"frozen_mean_{arm}.json")
            adapted = refit_scale(original, history[f"error_{arm}"], history["q"], spec)
            adapted.state["scale_oof_sha256"] = sha(train_dir / "oof_predictions.npz")
            save_json(model_path, adapted.state)
            groups[f"{spec['model_prefix']}_{arm}{suffix}"] = [adapted]
            groups[f"{spec['crossfit']['control_prefix']}_{arm}{suffix}"] = [original]
        scales = {
            k: np.array(v)
            for k, v in json.loads(
                (train_dir / "baseline_scales.json").read_text()
            ).items()
        }
        metrics, summary = forecast_phase(
            pool, spec, start, end, groups, None, scales, out / phase, recorder
        )
        s = summary[summary.horizon.eq(spec["primary_horizon"])].set_index("model")
        simple = min(
            ("B_TREND14", "PERSIST", "DRIFT14", *spec["extra_baselines"]),
            key=lambda name: s.loc[name, "rmse"],
        )
        decisions[phase] = gate(
            metrics, summary, spec["candidate"] + suffix, simple, spec
        )
        save_json(out / f"{phase}_decision.json", decisions[phase])
    # The development controls are identical to the already locked C4 distributions.
    reference = ROOT / spec["control_reference"]["path"]
    if (
        sha(reference / "artifact_manifest.json")
        != spec["control_reference"]["manifest_sha256"]
    ):
        raise ValueError("Frozen C4 control changed")
    identity = {}
    for path in (out / "development").glob("*.npz"):
        if path.stem.startswith(spec["model_prefix"]):
            continue
        name = path.name.replace(spec["crossfit"]["control_prefix"], "C4_RIDGE")
        with np.load(path) as a, np.load(reference / "development" / name) as b:
            for key in b.files:
                np.testing.assert_array_equal(a[key], b[key])
        identity[path.stem] = "all arrays exactly equal to C4"
    save_json(out / "control_identity.json", identity)
    decision = decisions["development"]
    decision.update(
        selected_alpha=selected,
        selected_updates=0,
        neural_updates=0,
        new_auxiliary_model_fits=new_auxiliary_fits,
        new_primary_mean_fits=0,
        new_primary_scale_fits=len(arms) * 4 * 2,
        eligible_for_transfer=False,
        assessment_scope="development only; C4 remains the single transfer candidate",
    )
    save_json(out / "decision.json", decision)
    recorder.event("development_decision", **decision)
    return decision
