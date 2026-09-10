"""Run or verify the fixed zero-fit temporal decomposition."""

import argparse
import contextlib
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
from physics_guided_temporal_features.workflow import NAMES, now
from .core import build_tables
from .workflow import (
    CONFIG,
    CONFIG_SHA,
    PLAN_SHA,
    load_daily,
    no_model_runtime,
    plot,
    source_guard,
    specification,
    verify,
)


def execute(out):
    started, spec = time.monotonic(), specification()
    protected = source_guard(spec)
    paths = [
        *sorted((ROOT / "code/physics_guided_temporal_decomposition").glob("*.py")),
        ROOT / "tests/test_physics_guided_temporal_decomposition.py",
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
            feature_names=NAMES,
            started_utc=now(),
            git_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
        ),
    )
    daily = load_daily(spec)
    for origin, data in daily.items():
        np.savez_compressed(out / f"prefix_{origin}.npz", **data)
    tables = build_tables(spec, daily, NAMES)
    for name, table in tables.items():
        table.to_csv(out / (name + ".csv"), index=False, lineterminator="\n")
    plot(out, spec, tables)
    seal(
        out / "execution.json", {k: v for k, v in spec.items() if k.startswith("new_")}
    )
    result = verify(out, sealed=False)
    seal(out / "verification.json", result)
    check_hashes(protected)
    check_hashes(sources)
    no_model_runtime()
    seal(
        out / "completed.json",
        dict(
            execution_complete=True,
            numerical_verification=True,
            completed_utc=now(),
            elapsed_seconds=time.monotonic() - started,
        ),
    )
    print(
        json.dumps(
            dict(status="completed", table_rows=result["table_rows"], new_fits=0)
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
        help="Read-only substitution; no fitting or writes",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.run_id):
        raise ValueError("Unsafe run id")
    out = ROOT / "results/ootang_bplus_v1_15" / args.run_id
    if args.verify:
        print(json.dumps(verify(out), indent=2))
        return
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    process = multiprocessing.get_context("spawn").Process(target=worker, args=(out,))
    process.start()
    process.join(specification()["timeout_seconds"])
    if process.is_alive():
        print(
            "Registered 120-second hard timeout reached; terminating worker.",
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
