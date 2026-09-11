"""One bounded diagnostic process, or read-only verification of a sealed archive."""

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
    array_sha,
    check_hashes,
    index_artifacts,
    load_observations,
    seal,
    sha,
)
from .core import build_tables
from .workflow import (
    CONFIG,
    CONFIG_SHA,
    PLAN_SHA,
    plot,
    prepare,
    source_guard,
    specification,
)


def now():
    return datetime.now(timezone.utc).isoformat()


def execute(out):
    from .verify import verify

    started, spec = time.monotonic(), specification()
    protected = source_guard(spec)
    paths = [
        *sorted((ROOT / "code/physics_guided_input_transfer").glob("*.py")),
        ROOT / "tests/test_physics_guided_input_transfer.py",
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
    prefixes, locked = {}, {}
    edges, references = 0, 0
    for text_h in spec["horizons"]:
        check_hashes(locked, out)
        h, read_utc = int(text_h), now()
        data, info = prepare(h, spec)
        for strategy, d in data.items():
            name = f"prefix_{h}_{strategy}.npz"
            np.savez_compressed(out / name, **d)
            locked[name] = sha(out / name)
            prefixes[(h, strategy)] = d
            edges += len(d["edges"])
            for lead in range(len(d["chosen"])):
                k = int(np.sum(d["targets"] - d["origins"] == lead))
                references += k * (k - 1) // 2
        info.update(label_read_utc=read_utc, locked_utc=now())
        name = f"inputs_{h}.json"
        seal(out / name, info)
        locked[name] = sha(out / name)
        print(
            json.dumps(
                dict(
                    fit_days=h,
                    choices_locked=True,
                    queries=2 * spec["horizons"][text_h],
                    neural_updates=0,
                )
            ),
            flush=True,
        )
    if edges != spec["candidate_edges"]:
        raise ArithmeticError("Primary candidate budget differs")
    seal(out / "selection_lock.json", dict(files=locked, locked_utc=now()))
    score_utc = now()
    _, _, labels = load_observations(ROOT / spec["source_run"] / "input.csv", 792)
    seal(
        out / "scoring.json",
        dict(
            label_read_utc=score_utc,
            label_rows=792,
            labels_sha256=array_sha(labels),
            selection_lock_sha256=sha(out / "selection_lock.json"),
        ),
    )
    tables = build_tables(prefixes, labels)
    if {k: len(v) for k, v in tables.items()} != dict(
        edges=7200, queries=3600, metrics=88
    ):
        raise ArithmeticError("Primary table budget differs")
    for name, frame in tables.items():
        frame.to_csv(out / f"{name}.csv", index=False, lineterminator="\n")
    plot(out, tables["queries"])
    counts = {
        k: spec[k]
        for k in (
            "neural_updates",
            "neural_forward_calls",
            "gradient_calls",
            "physical_forward_calls",
            "physical_optimizer_nfev",
            "regression_fits",
            "scaler_fits",
            "scale_fits",
        )
    }
    seal(
        out / "execution.json",
        dict(**counts, candidate_edges=edges, past_reference_pairs=references),
    )
    receipt = verify(out, sealed=False)
    seal(out / "verification.json", receipt)
    check_hashes(protected)
    check_hashes(sources)
    check_hashes(locked, out)
    seal(
        out / "completed.json",
        dict(
            execution_complete=True,
            numerical_verification=True,
            elapsed_seconds=time.monotonic() - started,
            completed_utc=now(),
        ),
    )
    print(
        json.dumps(
            dict(
                status="completed",
                verified=True,
                candidate_edges=edges,
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
        help="Read-only scalar replay; no updates or writes",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.run_id):
        raise ValueError("Unsafe run id")
    spec = specification()
    out = ROOT / "results/ootang_bplus_v1_22" / args.run_id
    if args.verify:
        from .verify import verify

        print(json.dumps(verify(out), indent=2))
        return
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    process = multiprocessing.get_context("spawn").Process(target=worker, args=(out,))
    process.start()
    process.join(spec["hard_timeout_seconds"])
    if process.is_alive():
        print(
            "Registered 300-second hard timeout reached; terminating worker.",
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
