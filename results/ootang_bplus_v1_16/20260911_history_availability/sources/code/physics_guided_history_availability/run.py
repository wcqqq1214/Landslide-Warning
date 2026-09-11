"""Execute or independently verify the fixed zero-fit history availability audit."""

import argparse
import contextlib
from datetime import datetime, timezone
import json
import multiprocessing
import platform
import re
import shutil
import subprocess
import sys
import time
import traceback

import numpy as np
import pandas as pd

from physics_guided_forecast_error.artifacts import (
    ROOT,
    check_hashes,
    index_artifacts,
    seal,
    sha,
)
from physics_guided_rate_diagnostics.workflow import no_model_runtime
from .verify import verify
from .workflow import (
    CONFIG,
    CONFIG_SHA,
    PLAN_SHA,
    build_outputs,
    source_audit,
    source_guard,
    specification,
)


def now():
    return datetime.now(timezone.utc).isoformat()


def execute(out):
    started, spec = time.monotonic(), specification()
    protected = source_guard(spec)
    paths = [
        *sorted((ROOT / "code/physics_guided_history_availability").glob("*.py")),
        ROOT / "tests/test_physics_guided_history_availability.py",
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
            runtime=dict(
                python=sys.version,
                executable=sys.executable,
                numpy=np.__version__,
                pandas=pd.__version__,
                platform=platform.platform(),
            ),
            git_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
        ),
    )
    seal(out / "source_checks.json", source_audit(spec))
    tables, histories, availability = build_outputs(spec)
    for name, frame in tables.items():
        frame.to_csv(out / (name + ".csv"), index=False, lineterminator="\n")
    for origin, history in histories.items():
        np.savez_compressed(
            out / f"history_{origin}.npz",
            **{k: history[k] for k in ("dates", "u", "du")},
        )
    seal(out / "availability.json", availability)
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
            dict(
                status="completed",
                table_rows=result["table_rows"],
                new_fits=0,
                raw_observation_as_of_verified="unknown",
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
        help="Read-only verification; no fits or writes",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.run_id):
        raise ValueError("Unsafe run id")
    spec = specification()
    out = ROOT / "results/ootang_bplus_v1_16" / args.run_id
    if args.verify:
        print(json.dumps(verify(out), indent=2))
        return
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    process = multiprocessing.get_context("spawn").Process(target=worker, args=(out,))
    process.start()
    process.join(spec["timeout_seconds"])
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
