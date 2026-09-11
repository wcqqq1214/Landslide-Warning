"""Execute one bounded frozen-weight diagnosis, or replay it without writes."""

import argparse
import contextlib
from datetime import datetime, timezone
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
    check_hashes,
    index_artifacts,
    seal,
    sha,
)
from .core import Budget, tables
from .workflow import (
    CONFIG,
    CONFIG_SHA,
    PLAN_SHA,
    evaluate_prefix,
    plot,
    source_guard,
    specification,
)


def now():
    return datetime.now(timezone.utc).isoformat()


def execute(out):
    from .verify import verify

    started, spec = time.monotonic(), specification()
    protected = source_guard(spec)
    source = ROOT / spec["source_run"]
    paths = [
        *sorted((ROOT / "code/physics_guided_origin_gradients").glob("*.py")),
        ROOT / "tests/test_physics_guided_origin_gradients.py",
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
    budget = Budget(spec["primary_gradient_calls"], spec["primary_difference_calls"])
    prefixes, locked, parameter_layout, expected_difference = {}, {}, None, 0
    for h in spec["prefixes"]:
        check_hashes(locked, out)
        read_utc = now()
        data, info, names = evaluate_prefix(source, h, spec, budget)
        if parameter_layout is not None and parameter_layout != names:
            raise ValueError("Parameter layout differs across prefixes")
        parameter_layout = names
        info.update(label_read_utc=read_utc, locked_utc=now())
        np.savez_compressed(out / f"prefix_{h}.npz", **data)
        seal(out / f"inputs_{h}.json", info)
        expected_difference += info["expected_difference_calls"]
        for name in (f"prefix_{h}.npz", f"inputs_{h}.json"):
            locked[name] = sha(out / name)
        prefixes[h] = data
        print(
            json.dumps(
                dict(
                    fit_days=h,
                    checkpoints=len(data["keys"]),
                    calls=budget.record(),
                    neural_updates=0,
                )
            ),
            flush=True,
        )
    if (
        budget.gradient_calls != spec["primary_gradient_calls"]
        or budget.backward_calls != budget.gradient_calls
        or budget.difference_calls != expected_difference
    ):
        raise ValueError("Primary call count differs")
    seal(out / "parameter_layout.json", parameter_layout)
    seal(out / "output_lock.json", dict(files=locked, locked_utc=now()))
    records, differences = tables(prefixes, parameter_layout, spec)
    records.to_csv(out / "gradients.csv", index=False, lineterminator="\n")
    differences.to_csv(out / "finite_differences.csv", index=False, lineterminator="\n")
    plot(out, records)
    seal(
        out / "execution.json",
        dict(
            **budget.record(),
            **{
                k: spec[k]
                for k in (
                    "neural_updates",
                    "physical_forward_calls",
                    "physical_optimizer_nfev",
                    "scaler_fits",
                    "probability_scale_fits",
                )
            },
        ),
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
            scientific_forward_calls=budget.record()["forward_calls"]
            + result["replay_calls"]["forward_calls"],
            scientific_backward_calls=budget.backward_calls
            + result["replay_calls"]["backward_calls"],
            completed_utc=now(),
            elapsed_seconds=time.monotonic() - started,
        ),
    )
    print(
        json.dumps(
            dict(status="completed", numerical_verification=True, neural_updates=0)
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
        help="Replay frozen gradients; no training or artifact writes",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.run_id):
        raise ValueError("Unsafe run id")
    out = ROOT / "results/ootang_bplus_v1_20" / args.run_id
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
            "Registered 600-second hard timeout reached; terminating worker.",
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
