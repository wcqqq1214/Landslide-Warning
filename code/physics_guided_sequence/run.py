"""Run the registered interface validation or replay its sealed records."""

import argparse
import contextlib
from datetime import datetime, timezone
import json
import multiprocessing
import platform
import re
import shutil
import subprocess
import time
import traceback

import numpy as np
import pandas as pd
import torch

from physics_guided_forecast_error.artifacts import (
    ROOT,
    check_hashes,
    check_index,
    index_artifacts,
    read_json,
    seal,
    sha,
)
from .validation import CONFIG, require, source_guard, specification, validate


def now():
    return datetime.now(timezone.utc).isoformat()


def source_files(spec):
    paths = list((ROOT / "code/physics_guided_sequence").glob("*.py"))
    paths += [
        ROOT / "tests/test_physics_guided_sequence.py",
        CONFIG,
        ROOT / spec["protocol"],
    ]
    return {str(path.relative_to(ROOT)): sha(path) for path in paths}


def compare_csv(expected, path):
    # Preserve tiny nonzero long-horizon gradients; no absolute tolerance here.
    pd.testing.assert_frame_equal(
        expected, pd.read_csv(path, float_precision="round_trip"), check_exact=True
    )


def worker(out):
    with (out / "run.log").open("x", buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                spec = specification()
                protected = source_guard(spec)
                sources = source_files(spec)
                for name in sources:
                    target = out / "sources" / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / name, target)
                seal(out / "protected_before.json", protected)
                seal(
                    out / "manifest.json",
                    dict(
                        specification=spec,
                        sources=sources,
                        started_utc=now(),
                        git_commit=subprocess.check_output(
                            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                        ).strip(),
                        dependencies=dict(
                            python=platform.python_version(),
                            numpy=np.__version__,
                            torch=torch.__version__,
                            pandas=pd.__version__,
                        ),
                    ),
                )
                started = time.monotonic()

                def completed(info, arrays, record, gradients, differences, counts):
                    folder = out / info["name"]
                    folder.mkdir()
                    np.savez_compressed(folder / "values.npz", **arrays)
                    seal(folder / "checks.json", record)
                    pd.DataFrame(gradients).to_csv(
                        folder / "gradients.csv", index=False
                    )
                    pd.DataFrame(differences).to_csv(
                        folder / "differences.csv", index=False
                    )
                    print(
                        json.dumps(dict(case=info["name"], counts=counts)), flush=True
                    )

                result = validate(spec, callback=completed)
                for key in ("records", "gradients", "differences", "fingerprints"):
                    result[key].to_csv(out / f"{key}.csv", index=False)
                for name, state in result["checkpoints"].items():
                    torch.save(state, out / f"{name}.pt")
                check_hashes(protected)
                check_hashes(sources)
                seal(out / "validation.json", result["summary"])
                seal(
                    out / "completed.json",
                    dict(completed_utc=now(), seconds=time.monotonic() - started),
                )
                print(json.dumps(result["summary"]), flush=True)
            except BaseException as error:
                traceback.print_exc()
                seal(out / "failed.json", dict(error=repr(error), failed_utc=now()))
                raise


def verify(out):
    check_index(out)
    require(
        read_json(out / "launcher.json")["exitcode"] == 0,
        "Original interface run did not complete",
    )
    manifest = read_json(out / "manifest.json")
    spec = specification()
    require(manifest["specification"] == spec, "Changed recorded specification")
    protected = source_guard(spec)
    require(
        protected == read_json(out / "protected_before.json"),
        "Changed protected inventory",
    )
    check_hashes(manifest["sources"])
    check_hashes(manifest["sources"], out / "sources")
    result = validate(spec)
    require(
        result["summary"] == read_json(out / "validation.json"),
        "Changed validation summary",
    )
    for key in ("records", "gradients", "differences", "fingerprints"):
        compare_csv(result[key], out / f"{key}.csv")
    values_checked = 0
    max_difference = 0.0
    for name, arrays in result["arrays"].items():
        folder = out / name
        with np.load(folder / "values.npz", allow_pickle=False) as saved:
            require(set(saved.files) == set(arrays), "Changed stored case arrays")
            for key, value in arrays.items():
                np.testing.assert_array_equal(value, saved[key])
                values_checked += value.size
                max_difference = max(
                    max_difference, float(np.max(np.abs(value - saved[key])))
                )
        record = result["records"].set_index("name").loc[name].to_dict()
        record["name"] = name
        require(record == read_json(folder / "checks.json"), "Changed per-case checks")
        for key in ("gradients", "differences"):
            selected = result[key].loc[result[key].name == name].reset_index(drop=True)
            compare_csv(selected, folder / f"{key}.csv")
    for name, expected in result["checkpoints"].items():
        saved = torch.load(out / f"{name}.pt", map_location="cpu", weights_only=True)
        require(
            set(saved) == set(expected)
            and all(torch.equal(expected[k], saved[k]) for k in expected),
            "Changed fixed untrained checkpoint",
        )
    check_hashes(protected)
    return dict(
        **result["summary"],
        replayed_array_values=values_checked,
        max_replay_difference=max_difference,
        protected_files_checked=len(protected),
        source_files_checked=len(manifest["sources"]),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.run_id), "Unsafe run id")
    spec = specification()
    out = ROOT / "results/ootang_bplus_v1_24" / args.run_id
    if args.verify:
        print(json.dumps(verify(out), indent=2))
        return
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    process = multiprocessing.get_context("spawn").Process(target=worker, args=(out,))
    process.start()
    while (
        process.is_alive() and time.monotonic() - started < spec["hard_timeout_seconds"]
    ):
        process.join(1)
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
