"""Independent, bounded TCN execution: prepare -> inner -> development -> later."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import signal
import time
import traceback

import numpy as np
import torch

from short_horizon.common import (
    ROOT,
    Recorder,
    load_spec,
    save_json,
    sha,
    now,
    array_sha,
)
from short_horizon.data import load_cache, training, query, observations
from short_horizon.models import RidgeMean
from short_horizon.evaluation import (
    calibrate,
    ErrorCalibration,
    score_set,
    quality,
    selection,
    gates,
)
from short_horizon.run import lock_phase, previous_prediction, fitted_means
from .models import ARMS, TCN, Scaling, objective, predict


def arrays(path):
    with np.load(path) as a:
        return {k: a[k].copy() for k in a.files}


def lock_code(root, config):
    current = {
        f"code/tcn_short_horizon/{name}.py": sha(
            ROOT / f"code/tcn_short_horizon/{name}.py"
        )
        for name in ("__init__", "models", "run")
    }
    current[str(Path(config).resolve().relative_to(ROOT))] = sha(config)
    path = root / "implementation_lock.json"
    if path.exists():
        if json.loads(path.read_text()) != current:
            raise ValueError("TCN implementation/config changed after preparation")
    else:
        save_json(path, current)


def prepare(spec, root, recorder):
    out = root / "prepared"
    out.mkdir(exist_ok=False)
    oldspec = dict(spec, output_root=spec["legacy_root"])
    cache = load_cache(oldspec)
    # Never pass oracle forcing or unanchored oracle arrays into a new learner.
    excluded = ("oracle", "b_raw")
    np.savez_compressed(
        out / "inputs.npz", **{k: v for k, v in cache.items() if k not in excluded}
    )
    save_json(
        out / "receipt.json",
        dict(
            input_sha256=sha(out / "inputs.npz"),
            rows=len(cache["origins"]),
            source=spec["legacy_root"] + "/prepared/inputs.npz",
            source_sha256=sha(ROOT / spec["legacy_root"] / "prepared/inputs.npz"),
            reused_verified_physical_cache=True,
            removed_fields=list(excluded),
            new_physical_fits=0,
            new_physical_forward_calls=0,
            forecast_inputs_are_origin_local=True,
        ),
    )
    recorder.event("prepared_inputs_locked", sha256=sha(out / "inputs.npz"))


def train_group(spec, root, phase, name, tr, scaling, steps, recorder):
    directory = root / phase / "training" / name
    directory.mkdir(parents=True, exist_ok=False)
    save_json(directory / "scaling.json", scaling.state)
    cfg = spec["neural"]
    if steps not in cfg["checkpoints"] or steps > cfg["max_updates_each"]:
        raise ValueError("Unscheduled update count")
    for seed in cfg["seeds"]:
        recorder.guard()
        registry_path = root / "fit_registry.json"
        registry = (
            json.loads(registry_path.read_text()) if registry_path.exists() else []
        )
        if (
            len(registry) >= cfg["max_fits"]
            or sum(r["updates"] for r in registry) + steps > cfg["max_total_updates"]
        ):
            raise RuntimeError("Frozen fit/update budget exhausted")
        seed_dir = directory / f"seed_{seed}"
        seed_dir.mkdir()
        torch.manual_seed(seed)
        rng = np.random.default_rng(seed)
        model = TCN(spec)
        init_hash = hashlib.sha256(
            b"".join(v.numpy().tobytes() for v in model.state_dict().values())
        ).hexdigest()
        optimizer = torch.optim.Adam(model.parameters(), lr=cfg["learning_rate"])
        entry = dict(
            phase=phase,
            model=name,
            seed=seed,
            status="running",
            updates=0,
            start_utc=now(),
            initialization_sha256=init_hash,
            training_origins_sha256=array_sha(tr["origins"]),
            scaling_sha256=sha(directory / "scaling.json"),
            parameter_count=sum(p.numel() for p in model.parameters()),
        )
        registry.append(entry)
        save_json(registry_path, registry)
        started, losses, batch_hash = time.monotonic(), [], hashlib.sha256()
        try:
            for step in range(1, steps + 1):
                recorder.guard()
                if time.monotonic() - started >= cfg["max_process_seconds"]:
                    raise TimeoutError("Frozen single-fit deadline reached")
                ids = rng.integers(0, len(tr["origins"]), size=cfg["batch_size"])
                batch_hash.update(ids.tobytes())
                data = scaling.tensors(tr, ids)
                optimizer.zero_grad(set_to_none=True)
                loss = objective(model, name, data, scaling)
                if not torch.isfinite(loss):
                    raise ArithmeticError("Nonfinite loss")
                loss.backward()
                if any(
                    p.grad is not None and not torch.isfinite(p.grad).all()
                    for p in model.parameters()
                ):
                    raise ArithmeticError("Nonfinite gradient")
                torch.nn.utils.clip_grad_norm_(model.parameters(), cfg["gradient_clip"])
                optimizer.step()
                entry["updates"] = step
                losses.append(dict(update=step, loss=float(loss.detach())))
                if step in cfg["checkpoints"]:
                    path = seed_dir / f"step_{step}.pt"
                    torch.save(model.state_dict(), path)
                    recorder.event(
                        "checkpoint_locked",
                        model=name,
                        seed=seed,
                        update=step,
                        sha256=sha(path),
                    )
                if step % 50 == 0:
                    save_json(seed_dir / "losses.json", losses)
                    save_json(registry_path, registry)
                    print(
                        f"{phase} {name} seed={seed} step={step} loss={float(loss.detach()):.8g}",
                        flush=True,
                    )
            entry.update(
                status="completed",
                end_utc=now(),
                elapsed_seconds=time.monotonic() - started,
                batches_sha256=batch_hash.hexdigest(),
            )
        except Exception as exc:
            entry.update(
                status="failed",
                end_utc=now(),
                error=repr(exc),
                batches_sha256=batch_hash.hexdigest(),
            )
            raise
        finally:
            save_json(seed_dir / "losses.json", losses)
            save_json(registry_path, registry)
    return directory


def load_group(directory, spec, step):
    scaling = Scaling(state=json.loads((directory / "scaling.json").read_text()))
    models = []
    for seed in spec["neural"]["seeds"]:
        model = TCN(spec)
        model.load_state_dict(
            torch.load(
                directory / f"seed_{seed}/step_{step}.pt",
                weights_only=True,
                map_location="cpu",
            )
        )
        models.append(model.eval())
    return models, scaling


def issue(spec, root, phase, means, cache, out, recorder, previous=None):
    predictions = {}
    start, end = spec["stages"][phase]
    labels, _, _ = observations(spec, end)
    out.mkdir(parents=True, exist_ok=True)
    for name, mean in means.items():
        prior = None if previous is None else previous.get(name)
        pred, sources = calibrate(
            spec, phase, name, mean, cache, prior, recorder, group=out.name
        )
        # Keep all seven issued endpoints even when some lie beyond the scoring window.
        cal = ErrorCalibration(
            labels[:start],
            start,
            prior,
            spec["calibration"]["window"],
            spec["calibration"]["sigma_floor_mm"],
        )
        issued_sigma = np.empty_like(mean)
        issued_locks = []
        for i, n in enumerate(pred["origins"]):
            issued_sigma[i] = cal.scale(n)
            issued_locks.append(array_sha(np.stack([mean[i], issued_sigma[i]])))
            for k in range(7):
                if i >= k:
                    cal.update(
                        k, labels[n] - mean[i - k, k], int(n - k), int(n), int(n + 1)
                    )
        valid = np.isfinite(pred["sigma"])
        np.testing.assert_array_equal(issued_sigma[valid], pred["sigma"][valid])
        np.savez_compressed(
            out / (name + "_issued.npz"),
            origins=pred["origins"],
            mean=mean,
            sigma=issued_sigma,
            locks=np.asarray(issued_locks),
        )
        np.savez_compressed(out / (name + ".npz"), **pred)
        save_json(
            out / (name + "_calibration.json"),
            dict(
                initialization=sources,
                probability_id=name + "_G",
                distribution="Normal(mean,matured_error_RMS^2)",
            ),
        )
        predictions[name] = pred
    return predictions


def stage_inputs(spec, cache, root, phase):
    start, end = spec["stages"][phase]
    out = root / phase
    out.mkdir(exist_ok=False)
    tr = training(cache, spec, start)
    data = query(cache, np.arange(start, end))
    scaling = Scaling(tr)
    save_json(out / "scaling.json", scaling.state)
    np.savez_compressed(
        out / "training_queries.npz",
        origins=tr["origins"],
        teacher=tr["teacher"],
        target_last=tr["origins"] + 6,
        neural_observation_first=tr["origins"] - 30,
    )
    old = ROOT / spec["legacy_root"] / phase / "RR_DIRECT_model.json"
    shutil.copyfile(old, out / old.name)
    ridge = RidgeMean(json.loads(old.read_text()))
    if ridge.state["alpha"] != spec["ridge"]["alpha"]:
        raise ValueError("Unexpected frozen ridge alpha")
    means = dict(
        B_ANCHOR=data["anchor"], DRIFT1=data["drift"], RR_DIRECT=ridge.predict(data)
    )
    return out, tr, data, scaling, means


def inner(spec, cache, root, recorder):
    out, tr, data, scaling, means = stage_inputs(spec, cache, root, "inner")
    controls = issue(spec, root, "inner", means, cache, out, recorder)
    paths = {
        name: train_group(spec, root, "inner", name, tr, scaling, 400, recorder)
        for name in ARMS
    }
    records = []
    for step in spec["neural"]["checkpoints"]:
        scores = {}
        for name in ARMS:
            models, scale = load_group(paths[name], spec, step)
            seeds = predict(models, name, scale, data)
            cp = out / f"{name}_step_{step}"
            pred = issue(
                spec, root, "inner", {name: seeds.mean(0)}, cache, cp, recorder
            )
            np.savez_compressed(cp / "seed_means.npz", mean=seeds)
            _, summary = score_set(spec, "inner", {**controls, **pred}, cp, common=True)
            scores[name] = quality(summary, name)
        records.append(
            dict(
                step=step,
                arms=scores,
                paired_score=float(np.mean(list(scores.values()))),
            )
        )
    best = min(r["paired_score"] for r in records)
    step = min(r["step"] for r in records if r["paired_score"] <= best + 1e-8)
    selected = dict(controls)
    for name in ARMS:
        models, scale = load_group(paths[name], spec, step)
        seeds = predict(models, name, scale, data)
        np.savez_compressed(out / (name + "_seed_means.npz"), mean=seeds)
        selected.update(
            issue(spec, root, "inner", {name: seeds.mean(0)}, cache, out, recorder)
        )
        fitted_means(out, name, predict(models, name, scale, tr).mean(0), tr, spec)
    score_set(spec, "inner", selected, out)
    score_set(spec, "inner", selected, out, common=True)
    lock = dict(
        step=step,
        candidates=records,
        selection_stage="inner",
        latest_selection_target=791,
        time_utc=now(),
        config_sha256=sha(ROOT / "config/ootang_tcn.v1_0.json"),
    )
    save_json(root / "internal_selection.json", lock)
    lock_phase(out)
    recorder.event(
        "internal_selection_locked",
        step=step,
        sha256=sha(root / "internal_selection.json"),
    )
    print(json.dumps(lock, ensure_ascii=False), flush=True)


def forecast_stage(spec, cache, root, phase, recorder):
    internal_path = root / "internal_selection.json"
    internal = json.loads(internal_path.read_text())
    if phase == "later_exploratory":
        frozen = json.loads((root / "selection.json").read_text())
        if frozen["internal_selection_sha256"] != sha(internal_path):
            raise ValueError("Internal selection changed after development")
        manifest = json.loads((root / "development/artifact_manifest.json").read_text())
        for name, digest in manifest.items():
            if sha(root / "development" / name) != digest:
                raise ValueError("Development artifact changed")
        recorder.event(
            "later_started_with_selection_lock",
            selection_sha256=sha(root / "selection.json"),
        )
    out, tr, data, scaling, means = stage_inputs(spec, cache, root, phase)
    for name in ARMS:
        directory = train_group(
            spec, root, phase, name, tr, scaling, internal["step"], recorder
        )
        models, scale = load_group(directory, spec, internal["step"])
        seeds = predict(models, name, scale, data)
        means[name] = seeds.mean(0)
        np.savez_compressed(out / (name + "_seed_means.npz"), mean=seeds)
        fitted_means(out, name, predict(models, name, scale, tr).mean(0), tr, spec)
    previous = {name: previous_prediction(root, phase, name) for name in means}
    predictions = issue(spec, root, phase, means, cache, out, recorder, previous)
    metrics, summary = score_set(spec, phase, predictions, out)
    score_set(spec, phase, predictions, out, common=True)
    if phase == "development":
        save_json(
            root / "selection.json",
            dict(
                source_stage=phase,
                time_utc=now(),
                latest_selection_target=1167,
                internal_selection_sha256=sha(internal_path),
                by_horizon=selection(spec, metrics, summary),
            ),
        )
        recorder.event(
            "development_selection_locked", sha256=sha(root / "selection.json")
        )
    else:
        checks = [
            dict(
                horizon=r["horizon"],
                mean_best=r["mean_best"],
                probability_best=r["probability_best"],
                recommended=r["recommended"],
                checks={
                    name: gates(spec, metrics, summary, name, r["horizon"])
                    for name in spec["models"]
                },
            )
            for r in frozen["by_horizon"]
        ]
        save_json(out / "frozen_selection_evaluation.json", checks)
    save_json(
        out / "status.json",
        dict(
            status="completed",
            models=spec["models"],
            selected_updates=internal["step"],
            all_planned_methods_evaluated=True,
            effect_failure_does_not_skip_later=True,
        ),
    )
    lock_phase(out)
    recorder.event("stage_completed", models=spec["models"])
    print(
        summary[["model", "horizon", "rmse", "crps", "coverage90"]].to_string(
            index=False
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--phase", choices=("prepare", "inner", "development", "later"), required=True
    )
    args = parser.parse_args()
    spec = load_spec(args.config)
    phase = "later_exploratory" if args.phase == "later" else args.phase
    root = ROOT / spec["output_root"]
    recorder = Recorder(root, spec, phase)
    recorder.guard()
    remaining = (
        datetime.fromisoformat(spec["deadline_utc"]) - datetime.now(timezone.utc)
    ).total_seconds()

    def timeout(*_):
        raise TimeoutError("Frozen total experiment deadline reached")

    signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, remaining)
    torch.set_num_threads(spec["neural"]["cpu_threads"])
    torch.use_deterministic_algorithms(True)
    lock_code(root, args.config)
    try:
        if phase == "prepare":
            prepare(spec, root, recorder)
        elif phase == "inner":
            inner(spec, load_cache(spec), root, recorder)
        else:
            forecast_stage(spec, load_cache(spec), root, phase, recorder)
    except Exception as exc:
        recorder.event(
            "phase_failed", error=repr(exc), traceback=traceback.format_exc()
        )
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == "__main__":
    main()
