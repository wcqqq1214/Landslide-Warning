"""Bounded frozen-weight validation of the shared mechanical prediction path."""

import argparse
import contextlib
import copy
from dataclasses import replace
import json
import multiprocessing
from pathlib import Path
import re
import shutil
import sys
import time
import traceback

import numpy as np
import pandas as pd
import torch
from torch.nn.utils import parameters_to_vector, vector_to_parameters

from physics_guided.data import Drivers
from physics_guided.reference import load as load_reference
from physics_guided.training import setup
from physics_guided_forecast_error.artifacts import (
    ROOT,
    check_hashes,
    check_index,
    index_artifacts,
    read_json,
    seal,
    sha,
)
from physics_guided_state_pinn.core import tensor
from physics_guided_state_pinn.workflow import (
    load_bundle,
    make_case,
    now,
    read_labels,
    specification as state_specification,
)
from .core import Budget, RateInputs, RateReplay, RecordedMechanics, objective

CONFIG = ROOT / "config/ootang_bplus_shared_mechanics.v1_11.json"
CONFIG_SHA = "c34b4f7733bf4c5942834fc73be2398b2d807f3dfbccc4fa70417481922fdd76"
PLAN_SHA = "72271010ca7b34dfe4f11692e06201a353aa2e5be4acced439b7c7c460e21288"


def specification():
    if sha(CONFIG) != CONFIG_SHA:
        raise ValueError("Frozen shared mechanics configuration changed")
    spec = read_json(CONFIG)
    if sha(ROOT / spec["protocol"]) != PLAN_SHA:
        raise ValueError("Frozen shared mechanics plan changed")
    return spec


def limits(spec):
    return {
        **{
            name: spec["max_" + name]
            for name in (
                "reference_forwards",
                "neural_evaluations",
                "trajectories",
                "day_forwards",
                "day_backwards",
                "reverse_passes",
            )
        },
        "reference_substeps": spec["reference_substeps"],
    }


def prepare(out, spec):
    source, diagnostic = ROOT / spec["source_run"], ROOT / spec["diagnostic_run"]
    check_index(source)
    check_index(diagnostic)
    if not read_json(diagnostic / "verification.json")["passed"]:
        raise ValueError("Preceding diagnostic verification is missing")
    protected = read_json(diagnostic / "protected_before.json")
    protected.update(read_json(diagnostic / "manifest.json")["sources"])
    protected.update(
        {str(p.relative_to(ROOT)): sha(p) for p in diagnostic.rglob("*") if p.is_file()}
    )
    for name in (
        "docs/ootang_bplus_pinn_consistency_results.v1.10.md",
        "code/physics_guided/reference.py",
    ):
        protected[name] = sha(ROOT / name)
    refdir = ROOT / spec["reference_directory"]
    provenance = read_json(refdir / "provenance.json")
    if provenance["archive_sha256"] != sha(ROOT / "section2d_v4.zip"):
        raise ValueError("Original reference archive changed")
    check_hashes(provenance["source_hashes"], refdir)
    adapted = refdir / "section2d_v4/physical_model_posix.py"
    if sha(adapted) != provenance["adapted_python_sha256"]:
        raise ValueError("Platform reference adapter changed")
    library = (
        refdir
        / "section2d_v4"
        / (
            "physical_solver.dylib"
            if sys.platform == "darwin"
            else "physical_solver.so"
        )
    )
    if sha(library) != provenance["library_sha256"]:
        raise ValueError("Reference library changed")
    for p in [refdir / n for n in provenance["source_hashes"]] + [
        adapted,
        library,
        refdir / "provenance.json",
    ]:
        protected[str(p.relative_to(ROOT))] = sha(p)
    check_hashes(protected)
    sources = {
        str(p.relative_to(ROOT)): sha(p)
        for p in [
            *Path(__file__).parent.glob("*.py"),
            ROOT / "tests/test_physics_guided_shared_mechanics.py",
        ]
    }
    for name in [*sources, str(CONFIG.relative_to(ROOT)), spec["protocol"]]:
        destination = out / "sources" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    seal(
        out / "manifest.json",
        dict(
            specification=spec,
            sources=sources,
            config_sha256=CONFIG_SHA,
            plan_sha256=PLAN_SHA,
            started_utc=now(),
            source_index_sha256=sha(source / "artifact_manifest.json"),
        ),
    )
    seal(out / "protected_before.json", protected)
    seal(out / "reference_provenance.json", provenance)
    return source, protected, sources


def read_model(source, h, seed, epoch, constants):
    checkpoint = torch.load(
        source / f"model_{h}_{seed}/e{epoch}.pt", weights_only=True, map_location="cpu"
    )
    if (
        any(
            checkpoint[k] != v
            for k, v in dict(prefix=h, seed=seed, epoch=epoch).items()
        )
        or checkpoint["constants"] != constants
    ):
        raise ValueError("Frozen model identity or constants differ")
    model = RateReplay()
    model.load_state_dict(
        {
            k: v
            for k, v in checkpoint["state_dict"].items()
            if k.startswith("rate_net.")
        },
        strict=True,
    )
    if sum(p.numel() for p in model.parameters()) != 548:
        raise ValueError("The frozen rate network changed")
    return model


class Recorder:
    def __init__(self, out, budget):
        self.out, self.budget, self.rows = out, budget, []

    def evaluate(
        self, name, model, inputs, solver, *, days=None, multiplier=None, **identity
    ):
        result = model(inputs, solver, self.budget, days=days, multiplier=multiplier)
        arrays = {k: v.detach().numpy().copy() for k, v in result.items()}
        arrays["parameters"] = (
            parameters_to_vector(model.parameters()).detach().numpy().copy()
        )
        if any(not np.isfinite(v).all() for v in arrays.values()):
            raise ArithmeticError("Nonfinite prediction artifact")
        np.savez_compressed(self.out / f"{name}.npz", **arrays)
        self.rows.append(
            dict(
                id=name, days=len(arrays["mean"]), neural=multiplier is None, **identity
            )
        )
        return result


def difference_row(spec, h, kind, direction, step, ad, values, masks, base_masks):
    fd = (values[0] - values[1]) / (2 * step)
    allowed = spec["direction_atol"] + spec["direction_rtol"] * max(abs(ad), abs(fd))
    return dict(
        prefix=h,
        kind=kind,
        direction=direction,
        step=step,
        ad=ad,
        fd=fd,
        plus=values[0],
        minus=values[1],
        absolute_error=abs(ad - fd),
        allowed=allowed,
        passed=abs(ad - fd) <= allowed,
        plus_branch_changes=int(np.count_nonzero(masks[0] != base_masks)),
        minus_branch_changes=int(np.count_nonzero(masks[1] != base_masks)),
    )


def derivatives(spec, source, h, model, inputs, solver, recorder):
    labels = tensor(read_labels(source / "input_prefix_792.csv", h))
    base = recorder.evaluate(
        f"train_{h}",
        model,
        inputs,
        solver,
        days=h,
        prefix=h,
        kind="train",
        seed=0,
        epoch=200,
    )
    total, parts = objective(inputs, base, labels)
    final = base["mean"][-1].sum() / 100
    recorder.budget.tick("reverse_passes")
    gradient = torch.cat(
        [
            g.reshape(-1)
            for g in torch.autograd.grad(
                total, tuple(model.parameters()), retain_graph=True
            )
        ]
    )
    recorder.budget.tick("reverse_passes")
    gamma_gradient = torch.autograd.grad(final, base["multiplier"])[0]
    if not torch.isfinite(gradient).all() or not torch.isfinite(gamma_gradient).all():
        raise ArithmeticError("Nonfinite full-history gradients")
    if gradient.norm() <= spec["nonzero_gradient_threshold"]:
        raise ArithmeticError("Total gradient direction is unavailable")
    original = parameters_to_vector(model.parameters()).detach().clone()
    random = torch.from_numpy(
        np.random.default_rng(spec["random_direction_seed"]).normal(size=len(original))
    )
    directions = {
        "gradient": gradient / gradient.norm(),
        "random": random / random.norm(),
    }
    early_direction = torch.full((4,), 0.5, dtype=torch.float64)
    early_ad = float(gamma_gradient[spec["early_day"]] @ early_direction)
    if abs(early_ad) <= spec["nonzero_gradient_threshold"]:
        raise ArithmeticError("No verified nonzero early-day direction")
    np.savez_compressed(
        recorder.out / f"derivatives_{h}.npz",
        parameters=original.numpy(),
        gradient=gradient.numpy(),
        multiplier_gradient=gamma_gradient.numpy(),
        gradient_direction=directions["gradient"].numpy(),
        random_direction=directions["random"].numpy(),
        early_direction=early_direction.numpy(),
        objective=np.asarray(float(total.detach())),
        data=np.asarray(float(parts["data"].detach())),
        prior=np.asarray(float(parts["rate_prior"].detach())),
        final_scalar=np.asarray(float(final.detach())),
    )
    base_masks = base["masks"].numpy()
    rows = []
    try:
        for key, direction in directions.items():
            ad = float(gradient @ direction)
            for step in spec["direction_steps"]:
                values, masks = [], []
                for sign, tag in ((1, "plus"), (-1, "minus")):
                    vector_to_parameters(
                        original + sign * step * direction, model.parameters()
                    )
                    with torch.no_grad():
                        out = recorder.evaluate(
                            f"param_{h}_{key}_{step:.0e}_{tag}",
                            model,
                            inputs,
                            solver,
                            days=h,
                            prefix=h,
                            seed=0,
                            epoch=200,
                            kind="parameter_probe",
                        )
                        value, _ = objective(inputs, out, labels)
                    values.append(float(value))
                    masks.append(out["masks"].numpy())
                rows.append(
                    difference_row(
                        spec, h, "parameter", key, step, ad, values, masks, base_masks
                    )
                )
    finally:
        vector_to_parameters(original, model.parameters())
    if not torch.equal(parameters_to_vector(model.parameters()), original):
        raise ArithmeticError(
            "Frozen parameters were changed by diagnostic perturbations"
        )
    values, masks = [], []
    for sign, tag in ((1, "plus"), (-1, "minus")):
        gamma = base["multiplier"].detach().clone()
        gamma[spec["early_day"]] += sign * spec["early_step"] * early_direction
        with torch.no_grad():
            out = recorder.evaluate(
                f"early_{h}_{tag}",
                model,
                inputs,
                solver,
                days=h,
                multiplier=gamma,
                prefix=h,
                seed=0,
                epoch=200,
                kind="early_probe",
            )
        values.append(float(out["mean"][-1].sum() / 100))
        masks.append(out["masks"].numpy())
    rows.append(
        difference_row(
            spec,
            h,
            "early",
            "day1",
            spec["early_step"],
            early_ad,
            values,
            masks,
            base_masks,
        )
    )
    changed = replace(
        inputs,
        x=inputs.x.clone(),
        rate=inputs.rate.clone(),
        background=inputs.background.clone(),
    )
    for value in (changed.x, changed.rate, changed.background):
        value[h:] += spec["future_perturbation"]
    altered_solver = copy.copy(solver)
    altered_solver.force, altered_solver.elastic = (
        solver.force.copy(),
        solver.elastic.copy(),
    )
    altered_solver.force[h:] += spec["future_perturbation"]
    altered_solver.elastic[h:] += spec["future_perturbation"]
    with torch.no_grad():
        recorder.evaluate(
            f"future_{h}",
            model,
            changed,
            altered_solver,
            days=h,
            prefix=h,
            seed=0,
            epoch=200,
            kind="future_isolation",
        )
    return rows


def execute(out, budget):
    started = time.monotonic()
    spec = specification()
    setup(0)
    source, protected, sources = prepare(out, spec)
    reference = load_reference(ROOT / spec["reference_directory"])
    recorder, rows, native = Recorder(out, budget), [], []
    for h in spec["prefixes"]:
        bundle = load_bundle(state_specification(), h)
        saved, _, forcing, y0 = bundle
        case = make_case(bundle, h)
        inputs = RateInputs.from_case(case)
        drivers = Drivers(pd.DatetimeIndex(pd.to_datetime(saved["dates"])), forcing, y0)
        solver = RecordedMechanics(reference, saved["theta"], drivers, budget)
        np.savez_compressed(
            out / f"reference_{h}.npz",
            mean=solver.u + y0,
            observation_matrix=solver.ctx.obs,
            length=solver.ctx.length,
            **solver.reference,
        )
        native.append(
            dict(
                prefix=h,
                path=str(solver.lib.library._name),
                sha256=sha(solver.lib.library._name),
            )
        )
        with torch.no_grad():
            zero = read_model(source, h, 0, 0, case.constants)
            recorder.evaluate(
                f"zero_{h}",
                zero,
                inputs,
                solver,
                prefix=h,
                seed=0,
                epoch=0,
                kind="zero",
            )
        for seed in spec["seeds"]:
            model = read_model(source, h, seed, 200, case.constants)
            with torch.no_grad():
                recorder.evaluate(
                    f"full_{h}_{seed}",
                    model,
                    inputs,
                    solver,
                    prefix=h,
                    seed=seed,
                    epoch=200,
                    kind="full",
                )
        if h in spec["direction_prefixes"]:
            model = read_model(source, h, 0, 200, case.constants)
            rows.extend(derivatives(spec, source, h, model, inputs, solver, recorder))
        progress = json.dumps(
            dict(prefix=h, elapsed_seconds=time.monotonic() - started, **budget.counts)
        )
        print(progress, flush=True)
        sys.__stdout__.write(progress + "\n")
        sys.__stdout__.flush()
    pd.DataFrame(rows).to_csv(
        out / "directional_checks.csv", index=False, lineterminator="\n"
    )
    seal(out / "evaluations.json", recorder.rows)
    seal(out / "native_libraries.json", native)
    seal(
        out / "execution.json",
        dict(
            **budget.counts,
            optimizer_updates=0,
            label_prefixes=spec["direction_prefixes"],
            elapsed_seconds=time.monotonic() - started,
        ),
    )
    check_hashes(protected)
    check_hashes(sources)
    from .verify import verify

    checked = verify(out, sealed=False)
    seal(out / "verification.json", checked)
    seal(
        out / "completed.json",
        dict(
            validation_complete=True,
            numerical_verification=checked["passed"],
            completed_utc=now(),
            elapsed_seconds=time.monotonic() - started,
            **budget.counts,
            optimizer_updates=0,
        ),
    )


def worker(out):
    budget = Budget(limits(specification()))
    with (out / "run.log").open("x", buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                execute(out, budget)
            except BaseException as error:
                traceback.print_exc()
                seal(
                    out / "failed.json",
                    dict(
                        error=repr(error),
                        failed_utc=now(),
                        **budget.counts,
                        optimizer_updates=0,
                    ),
                )
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.run_id):
        raise ValueError("Unsafe run id")
    spec = specification()
    out = ROOT / "results/ootang_bplus_v1_11" / args.run_id
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    process = multiprocessing.get_context("spawn").Process(target=worker, args=(out,))
    process.start()
    process.join(spec["timeout_seconds"])
    if process.is_alive():
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
        if not (out / "failed.json").exists():
            seal(
                out / "failed.json",
                dict(error="hard validation timeout", failed_utc=now()),
            )
    seal(
        out / "launcher.json",
        dict(exitcode=process.exitcode, wall_seconds=time.monotonic() - started),
    )
    index_artifacts(out)
    if process.exitcode != 0:
        raise SystemExit(process.exitcode or 1)
    print(json.dumps(read_json(out / "completed.json"), indent=2))


if __name__ == "__main__":
    main()
