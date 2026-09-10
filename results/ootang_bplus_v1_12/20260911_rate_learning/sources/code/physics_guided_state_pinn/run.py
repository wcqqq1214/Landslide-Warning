"""Execute the frozen state-PINN experiment once with a hard process deadline."""

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
import torch

from physics_guided.training import optimizer, setup
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
from physics_guided_pinn.run_substep_audit import verify as verify_traces
from physics_guided_pinn.trace import inputs_from_saved, integrate
from .core import PHYSICS_KEYS, StatePINN, losses, tensor
from .workflow import (
    CONFIG,
    CONFIG_SHA,
    PLAN_SHA,
    CUTOFFS,
    calibration,
    diagnostics,
    load_bundle,
    make_case,
    now,
    numpy_output,
    plot,
    projected_background,
    read_labels,
    replay_audit,
    scoring,
    specification,
)


def report(value):
    line = json.dumps(value, ensure_ascii=False)
    print(line, flush=True)
    sys.__stdout__.write(line + "\n")
    sys.__stdout__.flush()


def save_checkpoint(path, model, case, seed, epoch):
    if path.exists():
        raise FileExistsError(path)
    torch.save(
        dict(
            state_dict=model.state_dict(),
            constants=case.constants,
            prefix=case.h,
            seed=seed,
            epoch=epoch,
        ),
        path,
    )


def prepare(out, spec):
    trace_source = ROOT / spec["trace_source"]
    comparison = ROOT / spec["comparison_source"]
    physical = ROOT / spec["physical_source"]
    for source in (trace_source, comparison, physical):
        check_index(source)
    trace_check = verify_traces(trace_source)
    if not trace_check["passed"]:
        raise ValueError("The prerequisite real substep audit did not pass")
    protected = read_json(trace_source / "protected_before.json")
    protected.update(read_json(trace_source / "manifest.json")["sources"])
    protected.update(
        {
            str(p.relative_to(ROOT)): sha(p)
            for p in trace_source.rglob("*")
            if p.is_file()
        }
    )
    protected.update(
        {str(CONFIG.relative_to(ROOT)): CONFIG_SHA, spec["protocol"]: PLAN_SHA}
    )
    for name in (
        "code/physics_guided_state_pinn/core.py",
        "docs/ootang_bplus_pinn_substep_audit_results.v1.8.md",
        "docs/ootang_bplus_state_pinn_implementation.v1.9.md",
    ):
        protected[name] = sha(ROOT / name)
    check_hashes(protected)
    sources = {
        **read_json(comparison / "manifest.json")["sources"],
        **read_json(trace_source / "manifest.json")["sources"],
    }
    for p in [
        *Path(__file__).parent.glob("*.py"),
        *sorted((ROOT / "tests").glob("test_physics_guided_state_pinn*.py")),
    ]:
        sources[str(p.relative_to(ROOT))] = sha(p)
    for name in [*sources, str(CONFIG.relative_to(ROOT)), spec["protocol"]]:
        destination = out / "sources" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
    shutil.copy2(physical / "input_prefix_792.csv", out / "input_prefix_792.csv")
    native = trace_source / "native"
    build = read_json(native / "build.json")
    library = native / Path(build["command"][-1]).name
    seal(
        out / "manifest.json",
        dict(
            specification=spec,
            started_utc=now(),
            sources=sources,
            config_sha256=CONFIG_SHA,
            plan_sha256=PLAN_SHA,
            input_sha256=sha(out / "input_prefix_792.csv"),
            git_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            native_library=str(library.relative_to(ROOT)),
            native_library_sha256=sha(library),
            prerequisite_verification=trace_check,
            versions=dict(
                python=sys.version, numpy=np.__version__, torch=torch.__version__
            ),
        ),
    )
    seal(out / "protected_before.json", protected)
    return protected, sources, library


def execute(out, state, started):
    spec = specification()
    setup(0)
    protected, sources, library = prepare(out, spec)
    previous_lock = None
    columns = [
        "prefix",
        "seed",
        "epoch",
        "total",
        "data",
        "physics",
        "rate_prior",
        "physics_weight",
        *PHYSICS_KEYS,
        "gradient_norm",
        "elapsed_seconds",
    ]
    with (out / "training.csv").open("x", newline="", buffering=1) as log:
        writer = csv.DictWriter(log, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for h in spec["prefixes"]:
            if previous_lock is not None and not (out / previous_lock).exists():
                raise RuntimeError(
                    "Previous forecast must be locked before the next labels"
                )
            seal(
                out / f"label_access_{h}.json",
                dict(
                    prefix=h, opened_utc=now(), preceding_prediction_lock=previous_lock
                ),
            )
            labels = read_labels(out / "input_prefix_792.csv", h)
            seal(
                out / f"label_prefix_{h}.json",
                dict(prefix=h, array_sha256=array_sha(labels)),
            )
            if h in CUTOFFS:
                calibrated = calibration(out, spec, h, labels)
                for strategy, values in calibrated.items():
                    np.savez_compressed(
                        out / f"calibration_{h}_{strategy}.npz", **values
                    )
                seal(
                    out / f"calibration_{h}.json",
                    dict(
                        locked_utc=now(),
                        prefix=h,
                        source_prefix=CUTOFFS[h],
                        days=h - CUTOFFS[h],
                        files={
                            strategy: sha(out / f"calibration_{h}_{strategy}.npz")
                            for strategy in calibrated
                        },
                    ),
                )
                state["constant_scale_fits"] += len(calibrated)
            train_bundle, full_bundle = load_bundle(spec, h, h), load_bundle(spec, h)
            train_case = make_case(train_bundle, h)
            full_case = make_case(full_bundle, h, train_case.constants)
            if train_case.days != h or len(train_case.base) != (h - 1) * 64 + 1:
                raise ValueError("Training case contains a future substep")
            seal(out / f"constants_{h}.json", train_case.constants)
            locked = {}
            for seed in spec["seeds"]:
                setup(seed)
                model = StatePINN()
                if sum(p.numel() for p in model.parameters()) != spec["parameters"]:
                    raise ValueError(
                        "Model architecture differs from the frozen budget"
                    )
                directory = out / f"model_{h}_{seed}"
                directory.mkdir()
                op = optimizer(model)
                save_checkpoint(directory / "e0.pt", model, train_case, seed, 0)
                with torch.no_grad():
                    state["zero_check_evaluations"] += 1
                    zero = model(full_case)
                    mean_error = float(
                        abs(zero["mean"] - tensor(full_bundle[0]["mean"])).max()
                    )
                    state_error = float(abs(zero["state"] - full_case.base).max())
                    if mean_error > 1e-8 or state_error > 1e-8:
                        raise ArithmeticError("Zero correction does not reproduce B+")
                    seal(
                        directory / "zero_check.json",
                        dict(mean_max_error_mm=mean_error, state_max_error=state_error),
                    )
                    del zero
                label_tensor = tensor(labels)
                for epoch in range(1, spec["epochs"] + 1):
                    if state["update_attempts"] >= spec["max_neural_updates"]:
                        raise RuntimeError("Neural update budget exhausted")
                    state["update_attempts"] += 1
                    state["training_evaluations"] += 1
                    op.zero_grad(set_to_none=True)
                    output = model(train_case)
                    total, terms = losses(train_case, output, label_tensor, epoch)
                    total.backward()
                    gradients = [p.grad for p in model.parameters()]
                    if any(g is None or not torch.isfinite(g).all() for g in gradients):
                        raise ArithmeticError("Missing or nonfinite gradient")
                    norm = torch.nn.utils.clip_grad_norm_(
                        model.parameters(), 1.0, error_if_nonfinite=True
                    )
                    state["optimizer_step_calls"] += 1
                    op.step()
                    if not all(torch.isfinite(p).all() for p in model.parameters()):
                        raise ArithmeticError("Nonfinite trained parameters")
                    state["completed_updates"] += 1
                    row = dict(
                        prefix=h,
                        seed=seed,
                        epoch=epoch,
                        total=float(total.detach()),
                        gradient_norm=float(norm),
                        elapsed_seconds=time.monotonic() - started,
                        **{
                            name: float(value.detach())
                            if isinstance(value, torch.Tensor)
                            else value
                            for name, value in terms.items()
                        },
                    )
                    writer.writerow(row)
                    if epoch in spec["checkpoints"]:
                        save_checkpoint(
                            directory / f"e{epoch}.pt", model, train_case, seed, epoch
                        )
                    if epoch % 25 == 0:
                        report(
                            {
                                key: row[key]
                                for key in (
                                    "prefix",
                                    "seed",
                                    "epoch",
                                    "data",
                                    "physics",
                                    "total",
                                    "elapsed_seconds",
                                )
                            }
                        )
                    del output, total, terms, gradients
                with torch.no_grad():
                    state["final_prediction_evaluations"] += 1
                    prediction = numpy_output(model(full_case))
                np.savez_compressed(directory / "P.npz", **prediction)
                source, trace, _, y0 = full_bundle
                background = projected_background(source, prediction)
                arguments = inputs_from_saved(source, trace["length"])
                arguments["background"] = background
                if state["recorded_integrations"] >= spec["max_recorded_integrations"]:
                    raise RuntimeError("Mechanical replay budget exhausted")
                seal(
                    directory / "replay_call.json",
                    dict(
                        ordinal=state["recorded_integrations"] + 1,
                        started_utc=now(),
                        prefix=h,
                        seed=seed,
                        checkpoint_sha256=sha(directory / "e200.pt"),
                        background_sha256=array_sha(background),
                    ),
                )
                state["recorded_integrations"] += 1
                recorded = integrate(library, arguments)
                state["recorded_substeps"] += len(recorded["previous"])
                expected, audit = replay_audit(full_bundle, prediction, recorded)
                np.savez_compressed(
                    directory / "R.npz",
                    **recorded,
                    mean=expected["mean"],
                    background=background,
                )
                seal(directory / "replay_audit.json", audit)
                seal(
                    directory / "diagnostics.json",
                    diagnostics(full_case, prediction, expected["mean"]),
                )
                if not audit["passed"]:
                    raise ArithmeticError("R violates the frozen mechanical audit")
                locked[str(seed)] = {
                    name: sha(directory / name)
                    for name in ("e200.pt", "P.npz", "R.npz")
                }
                del prediction, recorded, model, op
            previous_lock = f"prefix_{h}_locked.json"
            seal(
                out / previous_lock,
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
                    updates=state["completed_updates"],
                    replays=state["recorded_integrations"],
                )
            )
    seal(
        out / "predictions_locked.json",
        dict(
            locked_utc=now(),
            epochs=200,
            primary_output="R",
            diagnostic_output="P",
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
    metrics, seeds, comparisons, curves = scoring(out, spec, labels)
    for name, frame in (
        ("metrics", metrics),
        ("seed_metrics", seeds),
        ("comparisons", comparisons),
    ):
        frame.to_csv(out / f"{name}.csv", index=False, lineterminator="\n")
    for (n, strategy), dist in curves.items():
        np.savez_compressed(out / f"distribution_{n}_{strategy}.npz", **dist)
    plot(out, curves, labels)
    seal(
        out / "execution.json",
        dict(
            **state,
            elapsed_seconds_before_verification=time.monotonic() - started,
            locked_utc=now(),
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
            completed_utc=now(),
            elapsed_seconds=time.monotonic() - started,
            execution_complete=True,
            numerical_verification=verified["passed"],
            **state,
        ),
    )


def worker(out):
    started = time.monotonic()
    state = dict(
        update_attempts=0,
        optimizer_step_calls=0,
        completed_updates=0,
        training_evaluations=0,
        zero_check_evaluations=0,
        final_prediction_evaluations=0,
        recorded_integrations=0,
        recorded_substeps=0,
        constant_scale_fits=0,
        parameter_fits=0,
        scale_neural_updates=0,
    )
    with (out / "run.log").open("x", buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                execute(out, state, started)
            except BaseException as error:
                traceback.print_exc()
                seal(
                    out / "failed.json",
                    dict(
                        error=repr(error),
                        failed_utc=now(),
                        elapsed_seconds=time.monotonic() - started,
                        **state,
                    ),
                )
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", args.run_id):
        raise ValueError("Unsafe run id")
    spec = specification()
    out = ROOT / "results/ootang_bplus_v1_9" / args.run_id
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
                dict(error="1,800 second hard deadline", failed_utc=now()),
            )
    seal(
        out / "launcher.json",
        dict(exitcode=process.exitcode, wall_seconds=time.monotonic() - started),
    )
    if process.exitcode != 0:
        if not (out / "failed.json").exists():
            seal(
                out / "failed.json",
                dict(error=f"Worker exited {process.exitcode}", failed_utc=now()),
            )
        index_artifacts(out)
        raise SystemExit(f"Run stopped; evidence preserved in {out}")
    index_artifacts(out)
    check_index(out)
    print(json.dumps(read_json(out / "completed.json"), indent=2))
    print(out)


if __name__ == "__main__":
    main()
