"""Run the bounded v1.12 learning experiment once; preserve every stopped run."""

import argparse
import contextlib
import csv
import json
import multiprocessing
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback

import numpy as np
import pandas as pd
import torch

from physics_guided.data import Drivers
from physics_guided.reference import load as load_reference
from physics_guided.training import setup
from physics_guided_forecast_error.artifacts import (
    ROOT,
    array_sha,
    check_hashes,
    check_index,
    index_artifacts,
    read_json,
    seal,
    sha,
)
from physics_guided_pinn.run_substep_audit import load_npz
from physics_guided_pinn.trace import compile_trace, inputs_from_saved, integrate
from physics_guided_shared_mechanics.core import (
    Budget,
    RateInputs,
    RateReplay,
    RecordedMechanics,
    objective,
)
from physics_guided_shared_mechanics.run import read_model
from physics_guided_state_pinn.core import tensor
from physics_guided_state_pinn.verify import NumericalChecks
from physics_guided_state_pinn.workflow import (
    CUTOFFS,
    load_bundle,
    make_case,
    now,
    projected_background,
    read_labels,
    specification as state_specification,
)
from .workflow import (
    CONFIG,
    CONFIG_SHA,
    PLAN_SHA,
    arrays,
    calibrate,
    compare_output,
    limits,
    mechanical_audit,
    plot,
    scoring,
    specification,
    training_solver,
)


def report(value):
    line = json.dumps(value, ensure_ascii=False)
    print(line, flush=True)
    sys.__stdout__.write(line + "\n")
    sys.__stdout__.flush()


def prepare(out, spec):
    prototype = ROOT / spec["prototype_run"]
    source = ROOT / spec["source_run"]
    checked = ROOT / spec["source_verification"]
    for path in (prototype, source, checked):
        check_index(path)
    if (
        not read_json(prototype / "verification.json")["passed"]
        or not read_json(checked / "verification.json")["passed"]
    ):
        raise ValueError("Verified source and shared prototype are required")
    if read_json(checked / "manifest.json")["source_index_sha256"] != sha(
        source / "artifact_manifest.json"
    ):
        raise ValueError("Historical complete verification does not bind to source")
    protected = read_json(prototype / "protected_before.json")
    protected.update(read_json(prototype / "manifest.json")["sources"])
    for path in (prototype, checked):
        protected.update(
            {str(p.relative_to(ROOT)): sha(p) for p in path.rglob("*") if p.is_file()}
        )
    protected["docs/ootang_bplus_shared_mechanics_results.v1.11.md"] = sha(
        ROOT / "docs/ootang_bplus_shared_mechanics_results.v1.11.md"
    )
    check_hashes(protected)
    # This preflight only hashes old observations; it does not parse future labels.
    sources = {
        name: digest for name, digest in protected.items() if name.startswith("code/")
    }
    for p in [
        *Path(__file__).parent.glob("*.py"),
        ROOT / "tests/test_physics_guided_rate_learning.py",
    ]:
        sources[str(p.relative_to(ROOT))] = sha(p)
    for name in [*sources, str(CONFIG.relative_to(ROOT)), spec["protocol"]]:
        target = out / "sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    shutil.copyfile(source / "input_prefix_792.csv", out / "input_prefix_792.csv")
    seal(out / "protected_before.json", protected)
    seal(
        out / "manifest.json",
        dict(
            specification=spec,
            config_sha256=CONFIG_SHA,
            plan_sha256=PLAN_SHA,
            sources=sources,
            source_index_sha256=sha(source / "artifact_manifest.json"),
            prototype_index_sha256=sha(prototype / "artifact_manifest.json"),
            input_sha256=sha(out / "input_prefix_792.csv"),
            started_utc=now(),
            git_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            versions=dict(
                python=sys.version, numpy=np.__version__, torch=torch.__version__
            ),
        ),
    )
    library = compile_trace(out / "native", timeout=spec["compile_timeout_seconds"])
    return source, protected, sources, library


def save_checkpoint(path, model, optimizer, case, seed, epoch, source_sha):
    with path.open("xb") as stream:
        torch.save(
            dict(
                state_dict=model.state_dict(),
                optimizer_state=optimizer.state_dict(),
                constants=case.constants,
                prefix=case.h,
                seed=seed,
                epoch=epoch,
                initial_checkpoint_sha256=source_sha,
            ),
            stream,
        )


def apply_update(total, model, optimizer, budget, spec):
    if budget.counts["neural_updates"] >= budget.limits["neural_updates"]:
        raise RuntimeError("Optimizer budget exhausted before backward")
    budget.tick("reverse_passes")
    total.backward()
    if any(
        p.grad is None or not torch.isfinite(p.grad).all() for p in model.parameters()
    ):
        raise ArithmeticError("Missing or nonfinite rate gradient")
    norm = torch.nn.utils.clip_grad_norm_(
        model.parameters(), spec["gradient_norm_clip"], error_if_nonfinite=True
    )
    budget.tick("neural_updates")
    optimizer.step()
    if any(not torch.isfinite(p).all() for p in model.parameters()):
        raise ArithmeticError("Nonfinite updated rate weights")
    return float(norm)


def replay_checkpoints(directory, inputs, solver, budget, spec):
    rows = []
    for epoch in spec["checkpoints"]:
        checkpoint = torch.load(
            directory / f"e{epoch}.pt", weights_only=True, map_location="cpu"
        )
        model = RateReplay()
        model.load_state_dict(checkpoint["state_dict"], strict=True)
        budget.tick("checkpoint_replays")
        with torch.no_grad():
            actual = arrays(model(inputs, solver, budget))
        np.savez_compressed(directory / f"replay_e{epoch}.npz", **actual)
        expected = load_npz(directory / f"reference_e{epoch}.npz")
        rows.append(
            dict(
                epoch=epoch,
                checkpoint_sha256=sha(directory / f"e{epoch}.pt"),
                reference_sha256=sha(directory / f"reference_e{epoch}.npz"),
                replay_sha256=sha(directory / f"replay_e{epoch}.npz"),
                **compare_output(actual, expected),
            )
        )
    seal(directory / "checkpoint_verification.json", dict(passed=True, rows=rows))


def train_one(
    out,
    source,
    spec,
    h,
    seed,
    train_case,
    full_case,
    train_engine,
    full_engine,
    full_bundle,
    labels,
    budget,
    writer,
    started,
    library,
):
    setup(seed)
    model = read_model(source, h, seed, 0, train_case.constants)
    inputs, full_inputs = (
        RateInputs.from_case(train_case),
        RateInputs.from_case(full_case),
    )
    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=spec["learning_rate"],
        betas=tuple(spec["adam_betas"]),
        eps=spec["adam_epsilon"],
        weight_decay=spec["weight_decay"],
    )
    directory = out / f"model_{h}_{seed}"
    directory.mkdir()
    initial_sha = sha(source / f"model_{h}_{seed}/e0.pt")
    save_checkpoint(
        directory / "e0.pt", model, optimizer, train_case, seed, 0, initial_sha
    )
    label_tensor = tensor(labels)
    try:
        for epoch in range(1, spec["epochs"] + 1):
            if budget.counts["neural_updates"] >= spec["max_neural_updates"]:
                raise RuntimeError("Training update limit reached before forward")
            optimizer.zero_grad(set_to_none=True)
            output = model(inputs, train_engine, budget)
            total, parts = objective(inputs, output, label_tensor)
            if epoch - 1 in spec["checkpoints"]:
                reference = arrays(output)
                np.savez_compressed(
                    directory / f"reference_e{epoch - 1}.npz", **reference
                )
                if epoch == 1:
                    check = NumericalChecks()
                    check.close(
                        reference["mean"], full_bundle[0]["mean"][:h], mean=True
                    )
                    check.close(reference["multiplier"], np.ones((h, 4)), atol=0)
                    seal(
                        directory / "zero_check.json",
                        dict(
                            passed=True,
                            max_mean_difference_mm=check.max_mean_difference_mm,
                        ),
                    )
            norm = apply_update(total, model, optimizer, budget, spec)
            row = dict(
                prefix=h,
                seed=seed,
                epoch=epoch,
                total=float(total.detach()),
                data=float(parts["data"].detach()),
                rate_prior=float(parts["rate_prior"].detach()),
                gradient_norm=norm,
                elapsed_seconds=time.monotonic() - started,
            )
            writer.writerow(row)
            if epoch in spec["checkpoints"]:
                save_checkpoint(
                    directory / f"e{epoch}.pt",
                    model,
                    optimizer,
                    train_case,
                    seed,
                    epoch,
                    initial_sha,
                )
            if epoch % 25 == 0:
                report(row)
            del output, total, parts
        with torch.no_grad():
            prediction = arrays(model(full_inputs, full_engine, budget))
        np.savez_compressed(directory / "S.npz", **prediction)
        np.savez_compressed(directory / "reference_e200.npz", **arrays(prediction, h))
        saved, trace, _, y0 = full_bundle
        background = projected_background(saved, prediction)
        arguments = inputs_from_saved(saved, trace["length"])
        arguments["background"] = background
        seal(
            directory / "replay_call.json",
            dict(
                prefix=h,
                seed=seed,
                started_utc=now(),
                ordinal=budget.counts["recorded_integrations"] + 1,
                checkpoint_sha256=sha(directory / "e200.pt"),
                background_sha256=array_sha(background),
            ),
        )
        budget.tick("recorded_integrations")
        budget.tick("recorded_substeps", (len(background) - 1) * 64)
        recorded = integrate(library, arguments)
        recorded["background"] = background
        recorded["mean"] = recorded["coordinates"] @ saved["observation_matrix"].T + y0
        np.savez_compressed(directory / "R_check.npz", **recorded)
        seal(
            directory / "mechanical_verification.json",
            mechanical_audit(full_bundle, prediction, recorded),
        )
        replay_checkpoints(directory, inputs, train_engine, budget, spec)
        return {
            name: sha(directory / name)
            for name in (
                "e200.pt",
                "S.npz",
                "R_check.npz",
                "checkpoint_verification.json",
                "mechanical_verification.json",
            )
        }
    except BaseException:
        save_checkpoint(
            directory / "stopped.pt",
            model,
            optimizer,
            train_case,
            seed,
            epoch,
            initial_sha,
        )
        raise


def execute(out, budget, started):
    spec = specification()
    setup(0)
    source, protected, sources, library = prepare(out, spec)
    reference = load_reference(ROOT / spec["reference_directory"])
    previous = None
    native = []
    columns = [
        "prefix",
        "seed",
        "epoch",
        "total",
        "data",
        "rate_prior",
        "gradient_norm",
        "elapsed_seconds",
    ]
    with (out / "training.csv").open("x", newline="", buffering=1) as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for h in spec["prefixes"]:
            if previous is not None and not (out / previous).exists():
                raise RuntimeError("Previous forecast is not locked")
            seal(
                out / f"label_access_{h}.json",
                dict(prefix=h, opened_utc=now(), preceding_prediction_lock=previous),
            )
            labels = read_labels(out / "input_prefix_792.csv", h)
            seal(
                out / f"label_prefix_{h}.json",
                dict(prefix=h, array_sha256=array_sha(labels)),
            )
            if h in CUTOFFS:
                values = calibrate(out, h, labels)
                np.savez_compressed(out / f"calibration_{h}_S.npz", **values)
                for strategy in ("P0", "R"):
                    shutil.copyfile(
                        source / f"calibration_{h}_{strategy}.npz",
                        out / f"calibration_{h}_{strategy}.npz",
                    )
                seal(
                    out / f"calibration_{h}.json",
                    dict(
                        prefix=h,
                        source_prefix=CUTOFFS[h],
                        days=h - CUTOFFS[h],
                        locked_utc=now(),
                        files={
                            strategy: sha(out / f"calibration_{h}_{strategy}.npz")
                            for strategy in ("P0", "R", "S")
                        },
                    ),
                )
            state_spec = state_specification()
            train_bundle, full_bundle = (
                load_bundle(state_spec, h, h),
                load_bundle(state_spec, h),
            )
            train_case = make_case(train_bundle, h)
            full_case = make_case(full_bundle, h, train_case.constants)
            seal(out / f"constants_{h}.json", train_case.constants)
            saved, _, forcing, y0 = full_bundle
            driver = Drivers(
                pd.DatetimeIndex(pd.to_datetime(saved["dates"])), forcing, y0
            )
            engine = RecordedMechanics(reference, saved["theta"], driver, budget)
            NumericalChecks().close(engine.u + y0, saved["mean"], mean=True)
            train_engine = training_solver(engine, h)
            native.append(
                dict(
                    prefix=h,
                    path=str(engine.lib.library._name),
                    sha256=sha(engine.lib.library._name),
                )
            )
            locked = {}
            for seed in spec["seeds"]:
                locked[str(seed)] = train_one(
                    out,
                    source,
                    spec,
                    h,
                    seed,
                    train_case,
                    full_case,
                    train_engine,
                    engine,
                    full_bundle,
                    labels,
                    budget,
                    writer,
                    started,
                    library,
                )
            previous = f"prefix_{h}_locked.json"
            seal(
                out / previous,
                dict(
                    prefix=h,
                    end_index_exclusive=h + 180,
                    locked_utc=now(),
                    predictions=locked,
                ),
            )
            report(
                dict(
                    prefix=h,
                    status="forecasts_locked",
                    updates=budget.counts["neural_updates"],
                    checkpoint_replays=budget.counts["checkpoint_replays"],
                )
            )
    seal(out / "native_libraries.json", native)
    seal(
        out / "predictions_locked.json",
        dict(
            primary_output="S",
            epochs=200,
            locked_utc=now(),
            prefix_locks={
                str(h): sha(out / f"prefix_{h}_locked.json") for h in spec["prefixes"]
            },
        ),
    )
    seal(
        out / "label_access_792.json",
        dict(
            prefix=792,
            opened_utc=now(),
            preceding_prediction_lock="predictions_locked.json",
        ),
    )
    labels = read_labels(out / "input_prefix_792.csv", 792)
    metrics, seeds, comparison, curves = scoring(out, source, labels)
    for name, frame in (
        ("metrics", metrics),
        ("seed_metrics", seeds),
        ("comparisons", comparison),
    ):
        frame.to_csv(out / f"{name}.csv", index=False, lineterminator="\n")
    for (n, strategy), values in curves.items():
        np.savez_compressed(out / f"distribution_{n}_{strategy}.npz", **values)
    plot(out, curves, labels)
    seal(
        out / "execution.json",
        dict(
            **budget.counts,
            constant_scale_fits=2,
            constant_scale_components=6,
            elapsed_seconds_before_verification=time.monotonic() - started,
        ),
    )
    from .verify import verify

    verified = verify(out, sealed=False)
    seal(out / "verification.json", verified)
    check_hashes(protected)
    check_hashes(sources)
    seal(
        out / "completed.json",
        dict(
            execution_complete=True,
            numerical_verification=verified["passed"],
            completed_utc=now(),
            elapsed_seconds=time.monotonic() - started,
            **budget.counts,
        ),
    )


def worker(out):
    budget = Budget(limits(specification()))
    started = time.monotonic()
    with (out / "run.log").open("x", buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                execute(out, budget, started)
            except BaseException as error:
                traceback.print_exc()
                seal(
                    out / "failed.json",
                    dict(
                        error=repr(error),
                        failed_utc=now(),
                        elapsed_seconds=time.monotonic() - started,
                        **budget.counts,
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
    out = ROOT / "results/ootang_bplus_v1_12" / args.run_id
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
    if process.exitcode != 0 and not (out / "failed.json").exists():
        seal(
            out / "failed.json",
            dict(
                error=f"Worker stopped with code {process.exitcode}", failed_utc=now()
            ),
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
