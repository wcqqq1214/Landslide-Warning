"""Execute the bounded v1.10 frozen-weight diagnostic; never train or integrate."""

import argparse
import contextlib
import json
import multiprocessing
from pathlib import Path
import shutil
import sys
import time
import traceback

import numpy as np
import pandas as pd
import torch

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
from physics_guided_pinn.run_substep_audit import load_npz
from physics_guided_state_pinn.core import StatePINN, losses, tensor
from physics_guided_state_pinn.verify import NumericalChecks
from physics_guided_state_pinn.workflow import (
    load_bundle,
    make_case,
    now,
    numpy_output,
    read_labels,
    specification as state_specification,
    POINTS,
)
from .core import (
    TERMS,
    get_vector,
    gradient_tables,
    gradient_vectors,
    inventory,
    motion_decomposition,
    norm,
    set_vector,
)

CONFIG = ROOT / "config/ootang_bplus_pinn_consistency.v1_10.json"
CONFIG_SHA = "f7086cb2b1beba9588eaef41893ee277a48d5ad1c8cb0cf6666a6da03f59bc77"
PLAN_SHA = "e3bd8bb273761215bdaea4d41f01eb5b2f51b013f68a59c54276827f2462b8bb"


def specification():
    if sha(CONFIG) != CONFIG_SHA:
        raise ValueError("Frozen diagnostic configuration changed")
    spec = read_json(CONFIG)
    if sha(ROOT / spec["protocol"]) != PLAN_SHA:
        raise ValueError("Frozen diagnostic plan changed")
    return spec


def prepare(out, spec):
    source = ROOT / spec["source_run"]
    checked = ROOT / spec["verification_run"]
    summary = ROOT / spec["summary_run"]
    for path in (source, checked, summary):
        check_index(path)
    binding = read_json(checked / "manifest.json")
    if (
        binding["source_index_sha256"] != sha(source / "artifact_manifest.json")
        or not read_json(checked / "verification.json")["passed"]
    ):
        raise ValueError("Source has no matching complete verification")
    protected = read_json(source / "protected_before.json")
    protected.update(read_json(source / "manifest.json")["sources"])
    for path in (source, checked, summary):
        protected.update(read_json(path / "manifest.json")["sources"])
        protected.update(
            {str(p.relative_to(ROOT)): sha(p) for p in path.rglob("*") if p.is_file()}
        )
    protected["docs/ootang_bplus_state_pinn_results.v1.9.md"] = sha(
        ROOT / "docs/ootang_bplus_state_pinn_results.v1.9.md"
    )
    m2 = ROOT / spec["m2_source"]
    m2_files = [
        m2 / "development/M2/selection.json",
        *[m2 / f"development/M2/mean_training_seed{s}.json" for s in (0, 1, 2)],
    ]
    for name in ("models.py", "mechanics.py", "mechanics.c", "training.py"):
        current = ROOT / "code/physics_guided" / name
        snapshot = m2 / "source_snapshot/code/physics_guided" / name
        if sha(current) != sha(snapshot):
            raise ValueError("M2 source differs from its historical training snapshot")
        m2_files.extend([current, snapshot])
    protected.update({str(p.relative_to(ROOT)): sha(p) for p in m2_files})
    check_hashes(protected)
    sources = {
        str(p.relative_to(ROOT)): sha(p)
        for p in [
            *Path(__file__).parent.glob("*.py"),
            ROOT / "tests/test_physics_guided_pinn_consistency.py",
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
        ),
    )
    seal(out / "protected_before.json", protected)
    return source, protected, sources


def tick(state, key, maximum):
    if state[key] >= maximum:
        raise RuntimeError(f"Diagnostic budget exhausted: {key}")
    state[key] += 1


def evaluate(model, case, state, spec):
    tick(state, "neural_evaluations", spec["max_neural_evaluations"])
    return model(case)


def directional_checks(
    model, case, labels, total_gradient, groups, identity, state, spec, out
):
    original = get_vector(model)
    rows = []
    for group, ids in groups.items():
        direction = np.zeros_like(original)
        length = norm(total_gradient[ids])
        fallback = length <= spec["zero_gradient_threshold"]
        values = (
            np.random.default_rng(spec["fallback_direction_seed"]).normal(size=len(ids))
            if fallback
            else total_gradient[ids]
        )
        direction[ids] = values / norm(values)
        ad = float(np.sum(total_gradient * direction))
        np.savez_compressed(
            out / f"direction_{identity['prefix']}_{group}.npz", direction=direction
        )
        try:
            for step in spec["direction_steps"]:
                objective = []
                for sign in (1, -1):
                    set_vector(model, original + sign * step * direction)
                    with torch.no_grad():
                        output = evaluate(model, case, state, spec)
                        total, _ = losses(case, output, tensor(labels), 200)
                        objective.append(float(total))
                fd = (objective[0] - objective[1]) / (2 * step)
                allowed = spec["direction_atol"] + spec["direction_rtol"] * max(
                    abs(ad), abs(fd)
                )
                rows.append(
                    dict(
                        **identity,
                        group=group,
                        step=step,
                        fallback_direction=fallback,
                        ad=ad,
                        fd=fd,
                        plus=objective[0],
                        minus=objective[1],
                        absolute_error=abs(ad - fd),
                        allowed=allowed,
                        passed=abs(ad - fd) <= allowed,
                    )
                )
        finally:
            set_vector(model, original)
        if not np.array_equal(get_vector(model), original):
            raise ArithmeticError(
                "Diagnostic perturbation changed the frozen parameters"
            )
    return rows


def decompose(source, h, seed, case, out, check):
    directory = source / f"model_{h}_{seed}"
    p = load_npz(directory / "P.npz")
    r = load_npz(directory / "R.npz")
    count = (h - 1) * 64
    ps = p["state"][: count + 1]
    rs = np.vstack([np.zeros((1, 24)), r["current"][:count]])
    elastic = case.loads[:, 4:8].numpy()
    if not np.array_equal(elastic, r["loads"][:count, 4:8]):
        raise ValueError("P and R motion loads differ")
    beta = ((1 / 64) / (case.coefficient.tau_motion + 1 / 64)).numpy()
    arrays, audit = motion_decomposition(
        ps, rs, elastic, beta, case.observation.numpy()
    )
    check.close(arrays["actual_observed"], p["mean"][:h] - r["mean"][:h], mean=True)
    np.savez_compressed(out / f"motion_{h}_{seed}.npz", **arrays)
    rows = []
    for j, point in enumerate(POINTS):
        row = dict(prefix=h, seed=seed, station=point)
        for key in (
            "plastic_driven_s_observed",
            "motion_defect_s_observed",
            "background_difference_observed",
            "actual_observed",
        ):
            values = arrays[key][30:, j]
            row[key + "_rms_mm"] = float(np.sqrt(np.mean(values**2)))
            row[key + "_end_mm"] = float(values[-1])
        rows.append(row)
    return rows, dict(prefix=h, seed=seed, **audit)


def execute(out, state):
    started = time.monotonic()
    spec = specification()
    setup(0)
    source, protected, sources = prepare(out, spec)
    training = pd.read_csv(source / "training.csv")
    term_rows, balances, directions, motion_rows, audits = [], [], [], [], []
    check = NumericalChecks()
    reference_inventory = None
    label_prefixes = []
    for h in spec["prefixes"]:
        labels = read_labels(source / "input_prefix_792.csv", h)
        label_prefixes.append(h)
        bundle = load_bundle(state_specification(), h, h)
        case = make_case(bundle, h)
        for seed in spec["seeds"]:
            directory = source / f"model_{h}_{seed}"
            for epoch in spec["checkpoints"]:
                identity = dict(prefix=h, seed=seed, epoch=epoch)
                checkpoint = torch.load(
                    directory / f"e{epoch}.pt", weights_only=True, map_location="cpu"
                )
                if (
                    any(checkpoint[key] != value for key, value in identity.items())
                    or checkpoint["constants"] != case.constants
                ):
                    raise ValueError("Checkpoint identity or training constants differ")
                model = StatePINN()
                model.load_state_dict(checkpoint["state_dict"], strict=True)
                metadata, groups = inventory(model)
                if len(groups["G"]) != 548 or len(groups["H"]) != 3028:
                    raise ValueError("Parameter groups changed")
                if reference_inventory is None:
                    reference_inventory = metadata
                    seal(out / "parameters.json", metadata)
                elif reference_inventory != metadata:
                    raise ValueError("Parameter ordering differs across checkpoints")
                output = evaluate(model, case, state, spec)
                total, terms = losses(case, output, tensor(labels), 200)
                values = {name: float(terms[name].detach()) for name in TERMS}
                if epoch < 200:
                    row = training[
                        (training.prefix == h)
                        & (training.seed == seed)
                        & (training.epoch == epoch + 1)
                    ].iloc[0]
                    for name in TERMS:
                        check.objective(np.array(values[name]), np.array(row[name]))
                else:
                    saved = load_npz(directory / "P.npz")
                    for name, value in numpy_output(output).items():
                        count = (
                            (h - 1) * 64 + 1
                            if name in ("state", "delta_substep")
                            else h
                        )
                        check.close(value, saved[name][:count], mean=name == "mean")
                vectors = gradient_vectors(
                    model,
                    terms,
                    lambda: tick(state, "gradient_calls", spec["max_gradient_calls"]),
                )
                rows, balance, gradient = gradient_tables(vectors, values, groups)
                np.savez_compressed(
                    out / f"gradients_{h}_{seed}_{epoch}.npz",
                    gradients=vectors,
                    values=np.array([values[k] for k in TERMS]),
                )
                term_rows.extend({**identity, **row} for row in rows)
                balances.append(
                    dict(
                        **identity,
                        **balance,
                        objective_lambda1=float(total.detach()),
                        original_next_weight=min(1.0, (epoch + 1) / 20),
                    )
                )
                del output, terms, total
                if (
                    h in spec["direction_prefixes"]
                    and seed == spec["direction_seed"]
                    and epoch == spec["direction_epoch"]
                ):
                    directions.extend(
                        directional_checks(
                            model,
                            case,
                            labels,
                            gradient,
                            groups,
                            identity,
                            state,
                            spec,
                            out,
                        )
                    )
                del model
            tick(state, "algebraic_decompositions", spec["algebraic_decompositions"])
            rows, audit = decompose(source, h, seed, case, out, check)
            motion_rows.extend(rows)
            audits.append(audit)
            progress = dict(
                prefix=h, seed=seed, elapsed_seconds=time.monotonic() - started, **state
            )
            line = json.dumps(progress)
            print(line, flush=True)
            sys.__stdout__.write(line + "\n")
            sys.__stdout__.flush()
    for name, rows in (
        ("gradient_terms", term_rows),
        ("gradient_balance", balances),
        ("directional_checks", directions),
        ("motion_components", motion_rows),
    ):
        pd.DataFrame(rows).to_csv(out / f"{name}.csv", index=False, lineterminator="\n")
    seal(out / "motion_checks.json", audits)
    m2 = ROOT / spec["m2_source"] / "development/M2"
    selection = read_json(m2 / "selection.json")
    m2_logs = []
    for seed in spec["seeds"]:
        log = read_json(m2 / f"mean_training_seed{seed}.json")
        if [r["epoch"] for r in log] != list(range(1, 201)):
            raise ValueError("Historical M2 training log incomplete")
        m2_logs.append(
            dict(
                seed=seed,
                first_update_pre_loss=log[0]["loss"],
                last_update_pre_loss=log[-1]["loss"],
            )
        )
    seal(
        out / "m2_history.json",
        dict(
            selection=selection,
            training=m2_logs,
            historical_source_matches=True,
            new_m2_calls=0,
        ),
    )
    seal(
        out / "execution.json",
        dict(
            **state,
            label_prefixes=label_prefixes,
            elapsed_seconds=time.monotonic() - started,
            original_checkpoint_max_mean_difference_mm=check.max_mean_difference_mm,
        ),
    )
    check_hashes(protected)
    check_hashes(sources)
    from .verify import verify

    verified = verify(out, sealed=False)
    seal(out / "verification.json", verified)
    seal(
        out / "completed.json",
        dict(
            diagnostic_complete=True,
            numerical_verification=verified["passed"],
            completed_utc=now(),
            elapsed_seconds=time.monotonic() - started,
            **state,
        ),
    )


def worker(out):
    state = dict(
        neural_evaluations=0,
        gradient_calls=0,
        algebraic_decompositions=0,
        optimizer_updates=0,
        native_calls=0,
    )
    with (out / "run.log").open("x", buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                execute(out, state)
            except BaseException as error:
                traceback.print_exc()
                seal(
                    out / "failed.json",
                    dict(error=repr(error), failed_utc=now(), **state),
                )
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not args.run_id or Path(args.run_id).name != args.run_id:
        raise ValueError("Unsafe run id")
    spec = specification()
    out = ROOT / "results/ootang_bplus_v1_10" / args.run_id
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
                dict(error="hard diagnostic timeout", failed_utc=now()),
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
