"""Run the bounded same-weight diagnostic, or independently verify its archive."""

import argparse
import contextlib
import importlib.metadata
import json
import multiprocessing
import re
import shutil
import subprocess
import time
import traceback

import numpy as np

from physics_guided_forecast_error.artifacts import (
    ROOT,
    array_sha,
    check_hashes,
    index_artifacts,
    load_observations,
    read_json,
    seal,
    sha,
)
from physics_guided_history_learning.core import windows
from physics_guided_history_learning.run import now
from physics_guided_synchronized_correction.core import read_teacher
from .core import queries, masked_inputs, evaluate, summary_arrays, build_tables
from .workflow import (
    CONFIG,
    CONFIG_SHA,
    PLAN_SHA,
    specification,
    source_guard,
    saved_scalers,
    frozen_model,
    plot,
)


def execute(out):
    from .verify import verify

    started, spec = time.monotonic(), specification()
    protected = source_guard(spec)
    source = ROOT / spec["source_run"]
    physical = (
        ROOT / read_json(source / "manifest.json")["specification"]["physical_source"]
    )
    paths = [
        *sorted((ROOT / "code/physics_guided_history_diagnostics").glob("*.py")),
        ROOT / "tests/test_physics_guided_history_diagnostics.py",
        CONFIG,
        ROOT / spec["protocol"],
    ]
    sources = {str(p.relative_to(ROOT)): sha(p) for p in paths}
    for name in sources:
        target = out / "sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    seal(out / "protected_before.json", protected)
    seal(
        out / "manifest.json",
        dict(
            specification=spec,
            config_sha256=CONFIG_SHA,
            plan_sha256=PLAN_SHA,
            sources=sources,
            started_utc=now(),
            git_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            dependencies={
                p: importlib.metadata.version(p)
                for p in ("numpy", "pandas", "torch", "matplotlib")
            },
        ),
    )
    calls, prefixes, locked = {"forward_calls": 0}, {}, {}
    for h in spec["prefixes"]:
        check_hashes(locked, out)
        read_utc = now()
        _, _, labels = load_observations(source / "input.csv", h)
        base, features = read_teacher(source / "input.csv", physical, h)
        scalers = saved_scalers(source, h)
        pairs, kind = queries(h)
        if len(pairs) != spec["query_counts"][str(h)]:
            raise ValueError("Registered query count differs")
        x = windows(base, features, labels, h, pairs, *scalers, "H")
        x0 = masked_inputs(x)
        info = dict(
            fit_days=h,
            label_read_utc=read_utc,
            labels_sha256=array_sha(labels),
            H_input_sha256=array_sha(x.numpy()),
            H0_input_sha256=array_sha(x0.numpy()),
        )
        result = dict(pairs=pairs, kind=kind, base=base)
        full, masked = [], []
        for seed in spec["seeds"]:
            model = frozen_model(source, h, seed)
            full.append(
                evaluate(
                    model,
                    x,
                    spec["primary_batch"],
                    calls,
                    spec["primary_forward_calls"],
                )
            )
            masked.append(
                evaluate(
                    model,
                    x0,
                    spec["primary_batch"],
                    calls,
                    spec["primary_forward_calls"],
                )
            )
        result.update(H=np.array(full), H0=np.array(masked))
        filename = f"prefix_{h}.npz"
        np.savez_compressed(out / filename, **result)
        info.update(output_sha256=sha(out / filename), output_locked_utc=now())
        seal(out / f"inputs_{h}.json", info)
        for name in (filename, f"inputs_{h}.json"):
            locked[name] = sha(out / name)
        prefixes[h] = result
        print(
            json.dumps(
                dict(
                    fit_days=h,
                    queries=len(pairs),
                    forward_calls=calls["forward_calls"],
                    neural_updates=0,
                )
            ),
            flush=True,
        )
    if calls["forward_calls"] != spec["primary_forward_calls"]:
        raise ValueError("Primary network call count differs")
    seal(out / "output_lock.json", dict(files=locked, locked_utc=now()))
    read_utc = now()
    _, _, labels = load_observations(source / "input.csv", 792)
    seal(
        out / "scoring.json",
        dict(
            label_read_utc=read_utc,
            label_rows=792,
            output_lock_sha256=sha(out / "output_lock.json"),
            labels_sha256=array_sha(labels),
        ),
    )
    data = {h: summary_arrays(d, d["base"], labels) for h, d in prefixes.items()}
    tables = build_tables(data)
    for name, table in tables.items():
        table.to_csv(out / f"{name}.csv", index=False, lineterminator="\n")
    plot(out, data)
    execution = {
        k: spec[k]
        for k in (
            "neural_updates",
            "gradient_calls",
            "physical_forward_calls",
            "physical_optimizer_nfev",
            "scaler_fits",
            "probability_scale_fits",
        )
    }
    seal(
        out / "execution.json",
        dict(primary_forward_calls=calls["forward_calls"], **execution),
    )
    result = verify(out, sealed=False)
    seal(out / "verification.json", result)
    check_hashes(protected)
    check_hashes(sources)
    seal(
        out / "completed.json",
        dict(
            execution_complete=True,
            numerical_verification=True,
            scientific_forward_calls=calls["forward_calls"]
            + result["replay_forward_calls"],
            completed_utc=now(),
            elapsed_seconds=time.monotonic() - started,
        ),
    )
    print(
        json.dumps(
            dict(
                status="completed",
                numerical_verification=True,
                scientific_forward_calls=408,
                neural_updates=0,
            )
        ),
        flush=True,
    )


def worker(out):
    with (out / "run.log").open("x", buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                execute(out)
            except BaseException as error:
                traceback.print_exc()
                seal(out / "failed.json", dict(error=repr(error), failed_utc=now()))
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Read-only replay; no training or artifact writes",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.run_id):
        raise ValueError("Unsafe run id")
    out = ROOT / "results/ootang_bplus_v1_18" / args.run_id
    if args.verify:
        from .verify import verify

        print(json.dumps(verify(out), indent=2))
        return
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    process = multiprocessing.get_context("spawn").Process(target=worker, args=(out,))
    process.start()
    process.join(specification()["hard_timeout_seconds"])
    if process.is_alive():
        print(
            "Registered 180-second hard timeout reached; terminating worker.",
            flush=True,
        )
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
    print(json.dumps(dict(run_directory=str(out), exitcode=process.exitcode)))
    raise SystemExit(process.exitcode or 0)


if __name__ == "__main__":
    main()
