"""Sequential v4 preparation, internal selection, development lock and later replay."""

import argparse
import json
from pathlib import Path
import traceback
import numpy as np
import pandas as pd
import torch

from .common import ROOT, CALLS, Recorder, load_spec, save_json, sha
from .data import prepare, load_cache, training, query
from .models import Scaling, RidgeMean, predict_neural
from .train import train_group, load_group, physical_audit
from .evaluation import calibrate, c16_means, score_set, quality, selection, gates

NEURAL = ("CL_DIRECT", "CL_BRES", "PINN_EQ", "PINN_NOEQ")


def fitted_means(out, name, mean, data, spec):
    """Descriptive fitting error, explicitly separate from issued forecasts."""
    path = Path(out) / "fitting"
    path.mkdir(exist_ok=True)
    np.savez_compressed(path / (name + ".npz"), origins=data["origins"], mean=mean)
    errors = mean - data["target"]
    rows = []
    for h in range(7):
        for p, point in enumerate(spec["points"]):
            e = errors[:, h, p]
            rows.append(
                dict(
                    model=name,
                    horizon=h + 1,
                    point=point,
                    n=len(e),
                    mae=float(abs(e).mean()),
                    rmse=float(np.sqrt(np.mean(e * e))),
                    interpretation="fitting_only_not_issued_forecast",
                )
            )
    pd.DataFrame(rows).to_csv(
        path / (name + "_metrics.csv"), index=False, float_format="%.15g"
    )


def lock_implementation(root):
    modules = (
        "__init__",
        "common",
        "physics",
        "data",
        "models",
        "evaluation",
        "train",
        "run",
    )
    current = {
        f"code/short_horizon/{m}.py": sha(ROOT / f"code/short_horizon/{m}.py")
        for m in modules
    }
    path = root / "implementation_lock.json"
    if path.exists():
        if json.loads(path.read_text()) != current:
            raise ValueError("Runtime implementation changed after first phase")
    else:
        save_json(path, current)


def lock_phase(out):
    paths = {
        str(p.relative_to(out)): sha(p)
        for p in sorted(out.rglob("*"))
        if p.is_file() and p.name != "artifact_manifest.json"
    }
    save_json(out / "artifact_manifest.json", paths)


def previous_prediction(root, phase, name):
    previous = {
        "inner": None,
        "development": "inner",
        "later_exploratory": "development",
    }[phase]
    if previous is None:
        return None
    path = root / previous / (name + ".npz")
    if not path.exists():
        return None
    with np.load(path) as a:
        return {k: a[k].copy() for k in ("origins", "mean")}


def output_predictions(spec, phase, means, cache, out, recorder, previous=None):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    predictions = {}
    for name, mean in means.items():
        prior = None if previous is None else previous.get(name)
        pred, sources = calibrate(
            spec, phase, name, mean, cache, prior, recorder, group=out.name
        )
        np.savez_compressed(out / (name + ".npz"), **pred)
        save_json(
            out / (name + "_calibration.json"),
            dict(
                initialization=sources,
                distribution="Normal(mean, matured_error_RMS^2)",
                probability_id=name + "_G",
            ),
        )
        predictions[name] = pred
    return predictions


def stage_data(cache, spec, phase):
    start, end = spec["stages"][phase]
    data = query(cache, np.arange(start, end))
    data["stage_end"] = end
    return data


def base_means(data):
    return {
        "B_ANCHOR": data["anchor"],
        "DRIFT1": data["drift"],
        "B_RAW": data["b_raw"],
        "B_ORACLE": data["oracle"],
    }


def inner(spec, cache, root, recorder):
    out = root / "inner"
    if out.exists():
        raise FileExistsError("Internal run exists; retain original attempt")
    out.mkdir()
    tr = training(cache, spec, 612)
    scaling = Scaling(tr)
    data = stage_data(cache, spec, "inner")
    np.savez_compressed(
        out / "training_queries.npz",
        origins=tr["origins"],
        teacher=tr["teacher"],
        target_last=tr["origins"] + 6,
    )
    save_json(out / "scaling.json", scaling.state)
    baseline = output_predictions(spec, "inner", base_means(data), cache, out, recorder)
    chosen = {}
    records = {}
    all_predictions = dict(baseline)
    for name in NEURAL:
        try:
            path = train_group(name, tr, scaling, spec, "inner", 400, recorder)
            candidates = []
            for step in spec["neural"]["checkpoints"]:
                models, scale = load_group(path, name, spec, step)
                seed_means, _ = predict_neural(models, name, scale, data, spec)
                cpdir = out / f"{name}_step_{step}"
                pred = output_predictions(
                    spec, "inner", {name: seed_means.mean(0)}, cache, cpdir, recorder
                )
                np.savez_compressed(cpdir / "seed_means.npz", mean=seed_means)
                _, summary = score_set(
                    spec, "inner", {**baseline, **pred}, cpdir, common=True
                )
                candidates.append(dict(step=step, score=quality(summary, name)))
            minimum = min(r["score"] for r in candidates)
            step = min(r["step"] for r in candidates if r["score"] <= minimum + 1e-8)
            chosen[name] = step
            records[name] = candidates
            models, scale = load_group(path, name, spec, step)
            seed_means, _ = predict_neural(models, name, scale, data, spec)
            pred = output_predictions(
                spec, "inner", {name: seed_means.mean(0)}, cache, out, recorder
            )
            all_predictions.update(pred)
            fit, _ = predict_neural(models, name, scale, tr, spec)
            fitted_means(out, name, fit.mean(0), tr, spec)
        except Exception as exc:
            records[name] = dict(
                status="failed", error=repr(exc), traceback=traceback.format_exc()
            )
            save_json(out / (name + "_failure.json"), records[name])
            recorder.event("model_group_failed", model=name, error=repr(exc))
            recorder.guard()
    ridge_scores = []
    for alpha in spec["ridge"]["alphas"]:
        recorder.guard()
        directory = out / f"ridge_a{alpha:g}"
        means = {}
        for name in ("RR_DIRECT", "RR_BRES"):
            model = RidgeMean.fit(tr, scaling, alpha, name)
            save_json(directory / (name + "_model.json"), model.state)
            means[name] = model.predict(data)
        pred = output_predictions(spec, "inner", means, cache, directory, recorder)
        _, summary = score_set(
            spec, "inner", {**baseline, **pred}, directory, common=True
        )
        ridge_scores.append(
            dict(
                alpha=alpha, score=float(np.mean([quality(summary, n) for n in means]))
            )
        )
    score = min(r["score"] for r in ridge_scores)
    alpha = min(r["alpha"] for r in ridge_scores if r["score"] <= score + 1e-8)
    for name in ("RR_DIRECT", "RR_BRES"):
        model = RidgeMean(
            json.loads((out / f"ridge_a{alpha:g}" / (name + "_model.json")).read_text())
        )
        save_json(out / (name + "_model.json"), model.state)
        all_predictions.update(
            output_predictions(
                spec, "inner", {name: model.predict(data)}, cache, out, recorder
            )
        )
        fitted_means(out, name, model.predict(tr), tr, spec)
    metrics, summary = score_set(spec, "inner", all_predictions, out)
    choice = dict(
        checkpoints=chosen,
        neural_candidates=records,
        ridge_alpha=alpha,
        ridge_candidates=ridge_scores,
        selection_stage="inner",
        latest_selection_target=791,
        calls=CALLS.copy(),
    )
    save_json(root / "internal_selection.json", choice)
    lock_phase(out)
    recorder.event(
        "internal_selection_locked",
        checkpoints=chosen,
        ridge_alpha=alpha,
        sha256=sha(root / "internal_selection.json"),
    )


def forecast_stage(spec, phase, cache, root, recorder):
    internal = json.loads((root / "internal_selection.json").read_text())
    if phase == "later_exploratory":
        frozen = json.loads((root / "selection.json").read_text())
        if frozen["internal_selection_sha256"] != sha(root / "internal_selection.json"):
            raise ValueError("Internal choice changed after development")
    out = root / phase
    if out.exists():
        raise FileExistsError("Stage exists; retain original attempt")
    out.mkdir()
    start, _ = spec["stages"][phase]
    tr = training(cache, spec, start)
    scaling = Scaling(tr)
    data = stage_data(cache, spec, phase)
    np.savez_compressed(
        out / "training_queries.npz",
        origins=tr["origins"],
        teacher=tr["teacher"],
        target_last=tr["origins"] + 6,
    )
    save_json(out / "scaling.json", scaling.state)
    means = base_means(data)
    statuses = {}
    details = {}
    physics = {}
    for name in NEURAL:
        if name not in internal["checkpoints"]:
            statuses[name] = "not_run_internal_fit_failed"
            continue
        try:
            step = internal["checkpoints"][name]
            path = train_group(name, tr, scaling, spec, phase, step, recorder)
            models, scale = load_group(path, name, spec, step)
            seeds, detail = predict_neural(models, name, scale, data, spec)
            means[name] = seeds.mean(0)
            details[name] = detail
            np.savez_compressed(out / (name + "_seed_means.npz"), mean=seeds)
            if detail:
                np.savez_compressed(
                    out / (name + "_states.npz"),
                    states=np.stack([d["states"] for d in detail]),
                    initial=np.stack([d["initial"] for d in detail]),
                )
            statuses[name] = "completed"
            fit, _ = predict_neural(models, name, scale, tr, spec)
            fitted_means(out, name, fit.mean(0), tr, spec)
        except Exception as exc:
            statuses[name] = "failed"
            save_json(
                out / (name + "_failure.json"),
                dict(error=repr(exc), traceback=traceback.format_exc()),
            )
            recorder.event("model_group_failed", model=name, error=repr(exc))
            recorder.guard()
    for name in ("RR_DIRECT", "RR_BRES"):
        recorder.guard()
        model = RidgeMean.fit(tr, scaling, internal["ridge_alpha"], name)
        save_json(out / (name + "_model.json"), model.state)
        means[name] = model.predict(data)
        statuses[name] = "completed"
        fitted_means(out, name, model.predict(tr), tr, spec)
    control, audit = c16_means(spec, phase, cache)
    means.update(control)
    for name in control:
        np.savez_compressed(out / (name + "_feedback_state.npz"), **audit[name])
        statuses[name] = "replayed"
    save_json(
        out / "c16_replay.json",
        dict(core_max_difference_mm=audit["core_replay_max_difference_mm"]),
    )
    previous = {name: previous_prediction(root, phase, name) for name in means}
    predictions = output_predictions(spec, phase, means, cache, out, recorder, previous)
    recorder.event(
        "all_predictions_locked",
        models=sorted(means),
        stage_end=spec["stages"][phase][1],
    )
    if "PINN_EQ" in means:
        physics["PINN_EQ"] = physical_audit(
            details["PINN_EQ"], means["PINN_EQ"], data, scaling, tr, spec, recorder
        )
        save_json(out / "PINN_EQ_physical_audit.json", physics["PINN_EQ"])
    metrics, summary = score_set(spec, phase, predictions, out)
    score_set(spec, phase, predictions, out, common=True)
    physical_pass = {n: r["passed"] for n, r in physics.items()}
    if phase == "development":
        lock = dict(
            source_stage=phase,
            latest_selection_target=1167,
            internal_selection_sha256=sha(root / "internal_selection.json"),
            by_horizon=selection(spec, metrics, summary, physical_pass),
            statuses=statuses,
            physical_pass=physical_pass,
            config_sha256=sha(
                ROOT / "config/ootang_short_horizon_comparison.v4_0.json"
            ),
        )
        save_json(root / "selection.json", lock)
    else:
        checks = []
        for row in frozen["by_horizon"]:
            n = row["recommended"]
            checks.append(
                dict(
                    horizon=row["horizon"],
                    development_recommended=n,
                    later_check=None
                    if n is None
                    else gates(
                        spec,
                        metrics,
                        summary,
                        n,
                        row["horizon"],
                        physical_pass.get(n, True),
                    ),
                )
            )
        save_json(out / "frozen_selection_evaluation.json", checks)
    save_json(
        out / "status.json",
        dict(models=statuses, physical_pass=physical_pass, calls=CALLS.copy()),
    )
    lock_phase(out)
    recorder.event("stage_completed", models=statuses, calls=CALLS.copy())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument(
        "--phase", choices=["prepare", "inner", "development", "later"], required=True
    )
    args = ap.parse_args()
    spec = load_spec(args.config)
    torch.set_num_threads(spec["neural"]["cpu_threads"])
    phase = "later_exploratory" if args.phase == "later" else args.phase
    root = ROOT / spec["output_root"]
    recorder = Recorder(root, spec, phase)
    recorder.guard()
    lock_implementation(root)
    try:
        if phase == "prepare":
            prepare(spec, recorder)
        else:
            cache = load_cache(spec)
            if phase == "inner":
                inner(spec, cache, root, recorder)
            else:
                forecast_stage(spec, phase, cache, root, recorder)
    except Exception as exc:
        recorder.event(
            "phase_failed",
            error=repr(exc),
            traceback=traceback.format_exc(),
            calls=CALLS.copy(),
        )
        raise


if __name__ == "__main__":
    main()
