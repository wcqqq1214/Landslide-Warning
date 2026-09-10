"""Archive one frozen-array diagnostic run; no training or model calls."""

import argparse
import contextlib
from datetime import datetime, timezone
import importlib.metadata
import json
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
from .workflow import (
    CONFIG,
    CONFIG_SHA,
    PLAN_SHA,
    build_tables,
    load_daily,
    no_model_runtime,
    plot,
    source_guard,
    specification,
)


def now():
    return datetime.now(timezone.utc).isoformat()


def execute(out):
    started = time.monotonic()
    spec = specification()
    protected = source_guard(spec)
    paths = [
        *sorted((ROOT / "code/physics_guided_rate_diagnostics").glob("*.py")),
        ROOT / "tests/test_physics_guided_rate_diagnostics.py",
        CONFIG,
        ROOT / spec["protocol"],
    ]
    sources = {str(p.relative_to(ROOT)): sha(p) for p in paths}
    for relative in sources:
        target = out / "sources" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    seal(out / "protected_before.json", protected)
    seal(
        out / "manifest.json",
        dict(
            specification=spec,
            config_sha256=CONFIG_SHA,
            plan_sha256=PLAN_SHA,
            sources=sources,
            source_run=spec["source_run"],
            source_index_sha256=spec["source_index_sha256"],
            git_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            started_utc=now(),
            versions={
                p: importlib.metadata.version(p)
                for p in ("numpy", "pandas", "matplotlib")
            },
        ),
    )
    print("Frozen source and implementation hashes checked.")
    daily = load_daily(spec)
    for h, values in daily.items():
        np.savez_compressed(out / f"prefix_{h}.npz", **values)
    for name, frame in build_tables(spec, daily).items():
        frame.to_csv(out / f"{name}.csv", index=False, lineterminator="\n")
    print("All registered descriptive tables saved; no models evaluated.")
    plot(out, daily)
    from .verify import verify

    verified = verify(out, sealed=False)
    seal(out / "verification.json", verified)
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
            **{key: value for key, value in verified.items() if key.startswith("new_")},
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.run_id):
        raise ValueError("Unsafe run id")
    out = ROOT / "results/ootang_bplus_v1_13" / args.run_id
    out.mkdir(parents=True, exist_ok=False)
    code, started = 0, time.monotonic()
    with (out / "run.log").open("x", buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                execute(out)
            except Exception as error:
                code = 1
                traceback.print_exc()
                seal(out / "failed.json", dict(error=repr(error), failed_utc=now()))
    seal(
        out / "launcher.json",
        dict(exitcode=code, wall_seconds=time.monotonic() - started),
    )
    index_artifacts(out)
    print(json.dumps(dict(run_directory=str(out), exitcode=code)))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
