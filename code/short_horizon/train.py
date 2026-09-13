"""Bounded, one-pass checkpoint training and physical output audits."""

import json
from pathlib import Path
import time
import numpy as np
import torch

from .common import ROOT, CALLS, save_json, sha, now
from .models import Scaling, new_model, objective
from .physics import replay, day_numpy


def train_group(name, data, scaling, spec, phase, steps, recorder):
    path = ROOT / spec["output_root"] / phase / "training" / name
    path.mkdir(parents=True, exist_ok=False)
    save_json(path / "scaling.json", scaling.state)
    checkpoints = [k for k in spec["neural"]["checkpoints"] if k <= steps]
    if steps not in checkpoints:
        checkpoints.append(steps)
    for seed in spec["neural"]["seeds"]:
        recorder.guard()
        seed_dir = path / f"seed_{seed}"
        seed_dir.mkdir()
        fit_registry = ROOT / spec["output_root"] / "fit_registry.json"
        registry = json.loads(fit_registry.read_text()) if fit_registry.exists() else []
        if len(registry) >= spec["neural"]["max_fits"]:
            raise RuntimeError("36-fit budget exhausted")
        entry = dict(
            phase=phase,
            model=name,
            seed=seed,
            status="running",
            start_utc=now(),
            updates=0,
        )
        registry.append(entry)
        save_json(fit_registry, registry)
        torch.manual_seed(seed)
        rng = np.random.default_rng(seed)
        model = new_model(name, spec)
        optimizer = torch.optim.Adam(
            model.parameters(), lr=spec["neural"]["learning_rate"]
        )
        start = time.monotonic()
        losses = []
        try:
            for step in range(1, steps + 1):
                recorder.guard()
                if time.monotonic() - start > spec["neural"]["max_process_seconds"]:
                    raise TimeoutError("Single-fit 15-minute limit reached")
                ids = rng.integers(
                    0, len(data["origins"]), size=spec["neural"]["batch_size"]
                )
                batch = scaling.tensors(data, ids)
                optimizer.zero_grad(set_to_none=True)
                loss, parts = objective(model, name, batch, scaling, spec)
                if not torch.isfinite(loss):
                    raise ArithmeticError("Nonfinite training loss")
                loss.backward()
                if any(
                    p.grad is not None and not torch.isfinite(p.grad).all()
                    for p in model.parameters()
                ):
                    raise ArithmeticError("Nonfinite gradient")
                torch.nn.utils.clip_grad_norm_(
                    model.parameters(), spec["neural"]["gradient_clip"]
                )
                optimizer.step()
                entry["updates"] = step
                if step == 1 or step % 25 == 0:
                    row = dict(update=step, loss=float(loss.detach()), **parts)
                    losses.append(row)
                    recorder.event(
                        "training_progress",
                        model=name,
                        seed=seed,
                        **row,
                        calls=CALLS.copy(),
                    )
                    save_json(seed_dir / "losses.json", losses)
                    save_json(fit_registry, registry)
                if step in checkpoints:
                    checkpoint = seed_dir / f"step_{step}.pt"
                    torch.save(model.state_dict(), checkpoint)
                    recorder.event(
                        "checkpoint_locked",
                        model=name,
                        seed=seed,
                        update=step,
                        sha256=sha(checkpoint),
                    )
            entry.update(
                status="completed",
                end_utc=now(),
                elapsed_seconds=time.monotonic() - start,
            )
        except Exception as exc:
            entry.update(status="failed", end_utc=now(), error=repr(exc))
            save_json(fit_registry, registry)
            recorder.event(
                "fit_failed",
                model=name,
                seed=seed,
                error=repr(exc),
                updates=entry["updates"],
            )
            raise
        save_json(fit_registry, registry)
    return path


def load_group(path, name, spec, step):
    path = Path(path)
    scaling = Scaling(state=json.loads((path / "scaling.json").read_text()))
    models = []
    for seed in spec["neural"]["seeds"]:
        model = new_model(name, spec)
        model.load_state_dict(
            torch.load(
                path / f"seed_{seed}" / f"step_{step}.pt",
                map_location="cpu",
                weights_only=True,
            )
        )
        model.eval()
        models.append(model)
    return models, scaling


def physical_audit(details, mean, data, scaling, training_data, spec, recorder):
    tolerance = np.minimum(
        spec["pinn"]["replay_tolerance_cap_mm"],
        spec["pinn"]["replay_tolerance_fraction_of_training_drift_rmse"]
        * np.sqrt(
            np.mean((training_data["target"] - training_data["drift"]) ** 2, axis=0)
        ).mean(axis=1),
    )
    all_details = details + [
        dict(
            states=np.mean([d["states"] for d in details], axis=0),
            initial=np.mean([d["initial"] for d in details], axis=0),
        )
    ]
    findings = []
    for seed, d in enumerate(all_details):
        max_difference = np.zeros(7)
        max_defect = 0.0
        min_plastic = 0.0
        max_gap = 0.0
        for i, n in enumerate(data["origins"]):
            if i % 50 == 0:
                recorder.guard()
            valid = min(7, int(data["stage_end"]) - int(n))
            states = d["states"][i, :valid]
            initial = d["initial"][i]
            exact, audits = replay(
                initial,
                states[:, 20:],
                data["force"][i, :valid],
                data["elastic"][i, :valid],
                data["coeff"][i],
            )
            coords = exact[:, :4] + exact[:, 20:] - (initial[:4] + initial[20:])
            expected = data["last_y"][i] + np.einsum(
                "hd,pd->hp", coords, data["obs"], optimize=False
            )
            network = data["last_y"][i] + np.einsum(
                "hd,pd->hp",
                states[:, :4] + states[:, 20:] - (initial[:4] + initial[20:]),
                data["obs"],
                optimize=False,
            )
            max_difference[:valid] = np.maximum(
                max_difference[:valid], abs(expected - network).max(axis=1)
            )
            for k in range(valid):
                old = initial if k == 0 else states[k - 1]
                flow, _, audit = day_numpy(
                    old,
                    states[k, 20:],
                    data["force"][i, k],
                    data["elastic"][i, k],
                    data["coeff"][i],
                )
                max_defect = max(
                    max_defect,
                    float(
                        np.max(abs((states[k, :20] - flow[:20]) / scaling.state_unit))
                    ),
                )
                min_plastic = min(min_plastic, float(np.min(states[k, 4:8] - old[4:8])))
                max_gap = max(max_gap, float(audit[2]))
        findings.append(
            dict(
                seed=seed if seed < 3 else "equal_weight_mean",
                max_replay_difference_mm=max_difference.tolist(),
                max_normalized_defect=max_defect,
                min_plastic_increment_mm=min_plastic,
                max_complementarity_residual=max_gap,
                passed=bool(
                    np.all(max_difference <= tolerance)
                    and max_defect <= spec["pinn"]["normalized_defect_max"]
                    and min_plastic >= -1e-8
                    and max_gap <= 1e-7
                ),
            )
        )
    return dict(
        passed=all(r["passed"] for r in findings),
        tolerance_by_horizon_mm=tolerance.tolist(),
        per_seed_and_ensemble=findings,
        main_output_replaced=False,
    )
