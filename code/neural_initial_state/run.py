"""One-pass fixed training, internal selection and conditional later evaluation."""

import argparse
import json
import shutil
import time
import traceback

import numpy as np
import torch

from short_horizon.common import ROOT, CALLS, Recorder, save_json, sha, now
from short_horizon.data import training, query
from short_horizon.models import Scaling
from short_horizon.evaluation import calibrate, score_set, quality

from .core import InitialStateNet, read_spec, cache_for, guard, objective, predict
from .feasibility import audit_prediction, independent_one, mean_from_state
from .reference import OriginalResume


def arrays(path):
    with np.load(path) as data:
        return {k: data[k].copy() for k in data.files}


def baselines(spec, phase):
    root = ROOT / spec["cache_root"] / phase
    return {
        name: arrays(root / (name + ".npz"))
        for name in spec["comparators"] if (root / (name + ".npz")).is_file()
    }


def lock_code(root, config):
    paths = [config] + [str(p.relative_to(ROOT)) for p in sorted((ROOT / "code/neural_initial_state").glob("*.py")) if p.name != "verify.py"]
    state = {p: sha(ROOT / p) for p in paths}
    target = root / "implementation_lock.json"
    if target.exists() and json.loads(target.read_text()) != state:
        raise ValueError("Training implementation changed between phases")
    save_json(target, state)


def train_once(data, scaling, spec, phase, steps, recorder):
    root = ROOT / spec["output_root"]
    out = root / phase / "training"
    out.mkdir()
    save_json(out / "scaling.json", scaling.state)
    registry_path = root / "fit_registry.json"
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else []
    for seed in spec["neural"]["seeds"]:
        guard(spec, "training")
        if len(registry) >= spec["neural"]["max_fits"]:
            raise RuntimeError("Nine-fit cap reached")
        if sum(r["updates"] for r in registry) + steps > spec["neural"]["max_total_updates"]:
            raise RuntimeError("Total fixed update cap reached")
        entry = dict(phase=phase, seed=seed, status="running", start_utc=now(), updates=0)
        registry.append(entry)
        save_json(registry_path, registry)
        torch.manual_seed(seed)
        rng = np.random.default_rng(seed)
        model = InitialStateNet()
        optimizer = torch.optim.Adam(model.parameters(), lr=spec["neural"]["learning_rate"])
        seed_path = out / f"seed_{seed}"
        seed_path.mkdir()
        torch.save(model.state_dict(), seed_path / "step_0.pt")
        start = time.monotonic()
        losses = []
        try:
            for step in range(1, steps + 1):
                guard(spec, "training")
                if time.monotonic() - start >= spec["neural"]["max_process_seconds"]:
                    raise TimeoutError("Fixed single-fit runtime cap reached")
                ids = rng.integers(0, len(data["origins"]), spec["neural"]["batch_size"])
                batch = scaling.tensors(data, ids)
                optimizer.zero_grad(set_to_none=True)
                loss, parts = objective(model, batch, scaling, spec)
                if not torch.isfinite(loss):
                    raise ArithmeticError("Nonfinite training loss")
                loss.backward()
                if any(p.grad is not None and not torch.isfinite(p.grad).all() for p in model.parameters()):
                    raise ArithmeticError("Nonfinite training gradient")
                norm = torch.nn.utils.clip_grad_norm_(model.parameters(), spec["neural"]["gradient_clip"])
                optimizer.step()
                entry["updates"] = step
                if step == 1 or step % 25 == 0 or step == steps:
                    row = dict(step=step, loss=float(loss.detach()), gradient_norm=float(norm), **parts)
                    losses.append(row)
                    save_json(seed_path / "losses.json", losses)
                    save_json(registry_path, registry)
                    recorder.event("training_progress", seed=seed, model=spec["model"], **row, calls=CALLS.copy())
                    if step % 100 == 0:
                        print(json.dumps(dict(phase=phase, seed=seed, **row)), flush=True)
                if step in spec["neural"]["checkpoints"] or step == steps:
                    target = seed_path / f"step_{step}.pt"
                    torch.save(model.state_dict(), target)
                    recorder.event("checkpoint_locked", seed=seed, step=step, sha256=sha(target))
            entry.update(status="completed", end_utc=now(), elapsed_seconds=time.monotonic() - start)
        except Exception as exc:
            entry.update(status="failed", error=repr(exc), end_utc=now(), elapsed_seconds=time.monotonic() - start)
            save_json(registry_path, registry)
            raise
        save_json(registry_path, registry)
    return out


def evaluate(spec, phase, cache, data, scaling, steps, previous, out, reference, recorder):
    phase_root = ROOT / spec["output_root"] / phase
    out.mkdir()
    means, details, physical = [], [], []
    for seed in spec["neural"]["seeds"]:
        model = InitialStateNet()
        model.load_state_dict(torch.load(phase_root / "training" / f"seed_{seed}" / f"step_{steps}.pt", map_location="cpu", weights_only=True))
        mu, detail = predict(model, scaling, data, spec)
        rows = audit_prediction(reference, data, detail, scaling.state_unit, spec)
        physical.append(dict(seed=seed, passed=True, trajectories=rows))
        means.append(mu)
        details.append(detail)
    means = np.stack(means)
    mean = means.mean(axis=0)
    np.savez_compressed(out / "seed_predictions.npz", origins=data["origins"], mean=means, **{k: np.stack([d[k] for d in details]) for k in details[0]})
    save_json(out / "physical_audit.json", dict(all_seeds_pass=True, seeds=physical))
    recorder.event("all_seed_trajectories_locked", steps=steps, path=str(out.relative_to(ROOT)), sha256=sha(out / "seed_predictions.npz"))
    # An ensemble average is statistical: inspect, do not substitute its nonlinear replay.
    averaged_initial = np.mean([d["initial"] for d in details], axis=0)
    discrepancy = np.zeros(7)
    for i in range(len(data["origins"])):
        guard(spec)
        states, _, _ = independent_one(reference, data, averaged_initial[i], i)
        q = mean_from_state(states, averaged_initial[i], data, i)
        discrepancy = np.maximum(discrepancy, abs(q - mean[i]).max(axis=1))
    save_json(out / "ensemble_interpretation.json", dict(statistical_mean_only=True, mean_initial_replay_difference_by_horizon_mm=discrepancy.tolist(), used_as_replacement=False, interval_physical_feasibility_claim=False))
    pred, init = calibrate(spec, phase, spec["model"], mean, cache, previous, recorder, group=out.name)
    np.savez_compressed(out / (spec["model"] + ".npz"), **pred)
    save_json(out / "calibration.json", dict(initialization=init, distribution="Normal(ensemble_mean,matured_error_RMS^2)", original_rule="v4.0"))
    predictions = {**baselines(spec, phase), spec["model"]: pred}
    metrics, summary = score_set(spec, phase, predictions, out)
    _, shared = score_set(spec, phase, predictions, out, common=True)
    return dict(q=quality(shared, spec["model"]), physics_pass=True, metrics=metrics, summary=summary, pred=pred)


def development_gates(spec, metrics, summary):
    rows = []
    cfg = spec["selection"]
    for h in spec["horizons"]:
        s = summary[summary.horizon == h].set_index("model")
        m = metrics[metrics.horizon == h].set_index(["model", "point"])
        candidate, base = s.loc[spec["model"]], s.loc["B_ANCHOR"]
        mean_checks = {k: bool(candidate[k] <= (1 - cfg["mean_relative_improvement"]) * base[k]) for k in ["mae", "rmse"]}
        probability_checks = {k: bool(candidate[k] <= (1 - cfg["probability_relative_improvement"]) * base[k]) for k in ["crps", "interval_score90"]}
        for point in spec["points"]:
            a, b = m.loc[(spec["model"], point)], m.loc[("B_ANCHOR", point)]
            for k in ["mae", "rmse"]:
                mean_checks[point + "_" + k] = bool(a[k] <= b[k] + cfg["point_mean_tolerance_mm"])
            for k in ["crps", "interval_score90"]:
                probability_checks[point + "_" + k] = bool(a[k] <= (1 + cfg["point_probability_max_regression"]) * b[k] + cfg["point_mean_tolerance_mm"])
        count = int(m.loc[(spec["model"], spec["points"][0]), "n"])
        probability_checks["coverage_deviation"] = bool(abs(candidate.coverage90 - .9) <= abs(base.coverage90 - .9) + 1 / count)
        M, P = all(mean_checks.values()), all(probability_checks.values())
        rows.append(dict(horizon=h, mean_pass=M, probability_pass=P, joint=M and P, average_rmse_no_worse_B=bool(candidate.rmse <= base.rmse + cfg["point_mean_tolerance_mm"]), mean_checks=mean_checks, probability_checks=probability_checks))
    passed = all(r["average_rmse_no_worse_B"] for r in rows) and sum(r["joint"] for r in rows) >= cfg["development_joint_horizons_min"]
    return dict(passed=bool(passed), joint_horizons=sum(r["joint"] for r in rows), horizons=rows)


def copy_selected(source, phase_root, name):
    for path in source.iterdir():
        if path.is_file():
            shutil.copy2(path, phase_root / path.name)
    return arrays(phase_root / (name + ".npz"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--phase", choices=["inner", "development", "later_exploratory"], required=True)
    args = parser.parse_args()
    spec = read_spec(args.config)
    guard(spec, "training")
    root = ROOT / spec["output_root"]
    if not json.loads((root / "feasibility/receipt.json").read_text())["passed"]:
        raise RuntimeError("Feasibility gate is not passed")
    selected = None
    if args.phase != "inner":
        selected = json.loads((root / "internal_selection.json").read_text())
        if not selected["passed"]:
            raise RuntimeError("Internal selection did not pass")
    if args.phase == "later_exploratory" and not json.loads((root / "development/decision.json").read_text())["passed"]:
        raise RuntimeError("Development failed; later training is prohibited")
    lock_code(root, args.config)
    out = root / args.phase
    out.mkdir(exist_ok=False)
    recorder = Recorder(root, spec, args.phase)
    torch.set_num_threads(spec["neural"]["cpu_threads"])
    try:
        cache = cache_for(spec)
        start, end = spec["stages"][args.phase]
        tr = training(cache, spec, start)
        scaling = Scaling(tr)
        data = query(cache, np.arange(start, end))
        np.savez_compressed(out / "training_queries.npz", origins=tr["origins"], teacher=tr["teacher"], target_last=tr["origins"] + 6)
        steps = spec["neural"]["max_updates_each"] if args.phase == "inner" else selected["step"]
        train_once(tr, scaling, spec, args.phase, steps, recorder)
        reference = OriginalResume(spec)
        previous_phase = {"inner": None, "development": "inner", "later_exploratory": "development"}[args.phase]
        previous = arrays(root / previous_phase / (spec["model"] + ".npz")) if previous_phase else None
        if args.phase == "inner":
            records = []
            for step in spec["neural"]["checkpoints"]:
                evaluated = evaluate(spec, args.phase, cache, data, scaling, step, previous, out / f"checkpoint_{step}", reference, recorder)
                records.append(dict(step=step, q=evaluated["q"], physics_pass=evaluated["physics_pass"]))
                print(json.dumps(records[-1]), flush=True)
            best = min(r["q"] for r in records)
            tol = spec["selection"]["inner_tolerance"]
            choice = min(r["step"] for r in records if r["q"] <= best + tol)
            zero = records[0]["q"]
            passed = choice != 0 and best < zero - tol and all(r["physics_pass"] for r in records)
            decision = dict(passed=bool(passed), step=choice, zero_q=zero, best_q=best, checkpoints=records, selection_uses="inner common 7-target origins only", later_reselection=False)
            copy_selected(out / f"checkpoint_{choice}", out, spec["model"])
            save_json(root / "internal_selection.json", decision)
        else:
            result = evaluate(spec, args.phase, cache, data, scaling, steps, previous, out / "selected", reference, recorder)
            copy_selected(out / "selected", out, spec["model"])
            decision = development_gates(spec, result["metrics"], result["summary"])
            decision.update(physics_pass=result["physics_pass"], step=steps, phase=args.phase, exploratory=True)
        save_json(out / "decision.json", decision)
        save_json(out / "completion.json", dict(status="completed", utc=now(), calls=CALLS.copy(), decision=decision))
        recorder.event("phase_completed", decision=decision, calls=CALLS.copy())
        print(json.dumps(decision, indent=2), flush=True)
    except Exception as error:
        save_json(out / "failure.json", dict(error=repr(error), traceback=traceback.format_exc(), utc=now(), calls=CALLS.copy()))
        recorder.event("phase_failed", error=repr(error), calls=CALLS.copy())
        raise


if __name__ == "__main__":
    main()
