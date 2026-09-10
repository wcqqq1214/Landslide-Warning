"""Run the finite v1.8 trace audit, or verify its sealed arrays without integration."""

import argparse
import contextlib
from datetime import datetime, timezone
import json
import multiprocessing
from pathlib import Path
import re
import shutil
import subprocess
import time
import traceback

import numpy as np
import pandas as pd
import torch

from physics_guided.data import POINTS
from physics_guided.reference import load
from physics_guided_diagnostics.run import REFERENCE, verify_reference
from physics_guided_forecast_error.artifacts import (
    ROOT,
    check_hashes,
    check_index,
    index_artifacts,
    read_json,
    seal,
    sha,
)
from .substep_audit import audit_trace
from .trace import compile_trace, inputs_from_saved, instrument, integrate, source_bytes

CONFIG = ROOT / "config/ootang_bplus_pinn_substep_audit.v1_8.json"
CONFIG_SHA = "a8b121fb1d5cac56e08333302a931eef2f84dc1a1a48acd1b9cfae057efed867"
PLAN_SHA = "4e34b807ba2dadc54faad053d950d4f6e1006980f220474155e3ed5d0e1c94c3"


def now():
    return datetime.now(timezone.utc).isoformat()


def specification():
    if sha(CONFIG) != CONFIG_SHA:
        raise ValueError("Frozen trace configuration changed")
    spec = read_json(CONFIG)
    if sha(ROOT / spec["protocol"]) != PLAN_SHA:
        raise ValueError("Frozen trace plan changed")
    return spec


def setup():
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)


def read_drivers(path):
    frame = pd.read_csv(path, nrows=792, usecols=["Date", "Rainfall/mm", "RWL/m"])
    columns = [p + "/mm" for p in POINTS]
    y0 = pd.read_csv(path, nrows=1, usecols=columns)[columns].to_numpy(float)[0]
    dates = pd.DatetimeIndex(pd.to_datetime(frame.Date, errors="raise"))
    forcing = frame[["Rainfall/mm", "RWL/m"]].to_numpy(float)
    if not dates.equals(pd.date_range("2016-07-01", periods=792)):
        raise ValueError("Unexpected driver calendar")
    if not np.isfinite(forcing).all() or not np.isfinite(y0).all():
        raise ValueError("Nonfinite drivers or first observation")
    if (
        (forcing[:, 0] < 0).any()
        or (forcing[:, 1] < 130).any()
        or (forcing[:, 1] > 190).any()
    ):
        raise ValueError("Drivers outside original forcing bounds")
    return dates.strftime("%Y-%m-%d").to_numpy(dtype="U10"), forcing, y0


def load_npz(path):
    with np.load(path, allow_pickle=False) as saved:
        return {name: saved[name].copy() for name in saved.files}


def validate_geometry(saved, ctx, tolerance):
    theta = saved["theta"]
    if theta.shape != (54,) or not np.isfinite(theta).all():
        raise ValueError("Invalid frozen parameters")
    kc = sum(np.exp(theta[39 + i]) * ctx.kt[i] for i in range(2))
    ke = 1000 * np.exp(theta[41]) * ctx.bulk
    for key, expected in (("observation_matrix", ctx.obs), ("kc", kc), ("ke", ke)):
        if not np.allclose(
            saved[key], expected, rtol=0, atol=tolerance["reaction_absolute"]
        ):
            raise ValueError(f"Frozen geometry/parameter mismatch: {key}")


def verify(out, sealed=True):
    """Read-only replay: original update algebra + residuals; zero native calls."""
    spec = specification()
    if sealed:
        check_index(out)
    manifest = read_json(out / "manifest.json")
    if manifest["specification"] != spec:
        raise ValueError("Recorded specification differs")
    check_hashes(manifest["sources"])
    check_hashes(read_json(out / "protected_before.json"))
    for path, digest in manifest["sources"].items():
        if sha(out / "sources" / path) != digest:
            raise ValueError(f"Scientific snapshot changed: {path}")
    if (
        sha(out / "sources" / CONFIG.relative_to(ROOT)) != CONFIG_SHA
        or sha(out / "sources" / spec["protocol"]) != PLAN_SHA
    ):
        raise ValueError("Protocol snapshot changed")
    physical = ROOT / spec["physical_source"]
    check_index(physical)
    verify_reference()
    dates, forcing, y0 = read_drivers(physical / spec["input_file"])
    drivers = load_npz(out / "drivers.npz")
    for name, expected in (("dates", dates), ("forcing", forcing), ("y0", y0)):
        if not np.array_equal(drivers[name], expected):
            raise ValueError(f"Saved driver metadata differs: {name}")
    raw = source_bytes()
    native = out / "native"
    if (native / "physical_solver.original.c").read_bytes() != raw or (
        native / "physical_solver.trace.c"
    ).read_text() != instrument(raw):
        raise ValueError(
            "Recorded instrumentation differs from the pinned transformation"
        )
    build = read_json(native / "build.json")
    for key, path in (
        ("original_sha256", native / "physical_solver.original.c"),
        ("derived_sha256", native / "physical_solver.trace.c"),
        ("library_sha256", native / Path(build["command"][-1]).name),
    ):
        if sha(path) != build[key]:
            raise ValueError("Compiled trace provenance changed")
    ctx = load(REFERENCE).Context(forcing)
    reports = {}
    for item in spec["trajectories"]:
        saved = load_npz(physical / item["file"])
        if not np.array_equal(saved["dates"], dates[: item["days"]]):
            raise ValueError("Saved trajectory calendar differs")
        validate_geometry(saved, ctx, spec["tolerances"])
        recorded = load_npz(out / (item["name"] + ".npz"))
        for name, expected in (
            ("length", ctx.length),
            ("observation_matrix", ctx.obs),
            ("y0", y0),
            ("theta", saved["theta"]),
            ("dates", saved["dates"]),
        ):
            if not np.array_equal(recorded[name], expected):
                raise ValueError(f"Trace metadata differs: {name}")
        actual = audit_trace(
            recorded, saved, ctx.length, ctx.obs, y0, spec["tolerances"]
        )
        stored = read_json(out / (item["name"] + ".audit.json"))
        if actual != stored:
            raise ValueError(
                "Independent read-only audit differs from the stored report"
            )
        reports[item["name"]] = actual["passed"]
    execution = read_json(out / "execution.json")
    starts = sorted(out.glob("*.call.json"))
    if (
        len(starts) != 3
        or execution["recorded_integrations"] != 3
        or execution["substeps"] != spec["total_substeps"]
    ):
        raise ValueError("Native execution count differs from the finite budget")
    for ordinal, item in enumerate(spec["trajectories"], 1):
        called = read_json(out / (item["name"] + ".call.json"))
        if called["ordinal"] != ordinal:
            raise ValueError("Integration call order differs")
        if called["trajectory"] != item or called["source_sha256"] != sha(
            physical / item["file"]
        ):
            raise ValueError("Original integration call record differs")
    for key in (
        "original_forward_calls",
        "independent_integrations",
        "parameter_fits",
        "neural_updates",
    ):
        if execution[key] != 0:
            raise ValueError("Unregistered scientific compute recorded")
    return dict(
        passed=all(reports.values()),
        trajectories=reports,
        substeps=spec["total_substeps"],
        reports_match_exactly=True,
        protected_files=len(read_json(out / "protected_before.json")),
        scientific_sources=len(manifest["sources"]),
        native_calls_during_verification=0,
    )


def execute(out, started):
    spec = specification()
    physical, prior = ROOT / spec["physical_source"], ROOT / spec["protected_source"]
    check_index(physical)
    check_index(prior)
    protected = read_json(prior / "protected_before.json")
    protected.update(read_json(prior / "manifest.json")["sources"])
    protected.update(
        {str(p.relative_to(ROOT)): sha(p) for p in prior.rglob("*") if p.is_file()}
    )
    protected.update(
        {str(CONFIG.relative_to(ROOT)): CONFIG_SHA, spec["protocol"]: PLAN_SHA}
    )
    for name in (
        "ootang_bplus_pinn_equation_contract.v1.8.md",
        "ootang_bplus_pinn_equation_validation.v1.8.md",
    ):
        p = ROOT / "docs" / name
        protected[str(p.relative_to(ROOT))] = sha(p)
    check_hashes(protected)
    source_paths = list(Path(__file__).parent.glob("*.py"))
    source_paths += list((ROOT / "tests").glob("test_physics_guided_pinn*.py"))
    source_paths += [
        ROOT / name
        for name in (
            "code/physics_guided/reference.py",
            "code/physics_guided/data.py",
            "code/physics_guided_diagnostics/run.py",
            "code/physics_guided_forecast_error/artifacts.py",
            "code/physics_guided_forecast_error/core.py",
        )
    ]
    sources = {str(p.relative_to(ROOT)): sha(p) for p in sorted(source_paths)}
    for name in [*sources, str(CONFIG.relative_to(ROOT)), spec["protocol"]]:
        destination = out / "sources" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
    provenance = verify_reference()
    seal(
        out / "manifest.json",
        dict(
            specification=spec,
            started_utc=now(),
            sources=sources,
            config_sha256=CONFIG_SHA,
            plan_sha256=PLAN_SHA,
            input_sha256=sha(physical / spec["input_file"]),
            git_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            reference_provenance=provenance,
            versions={
                "numpy": np.__version__,
                "torch": torch.__version__,
                "pandas": pd.__version__,
            },
        ),
    )
    seal(out / "protected_before.json", protected)
    dates, forcing, y0 = read_drivers(physical / spec["input_file"])
    np.savez_compressed(out / "drivers.npz", dates=dates, forcing=forcing, y0=y0)
    ctx = load(REFERENCE).Context(forcing)
    library = compile_trace(out / "native", spec["compile_timeout_seconds"])
    calls, steps = 0, 0
    for item in spec["trajectories"]:
        source = physical / item["file"]
        saved = load_npz(source)
        if saved["mean"].shape != (item["days"], 4) or not np.array_equal(
            saved["dates"], dates[: item["days"]]
        ):
            raise ValueError("Frozen trajectory shape or dates changed")
        validate_geometry(saved, ctx, spec["tolerances"])
        if calls >= spec["max_recorded_integrations"]:
            raise RuntimeError("Recorded integration budget exhausted")
        seal(
            out / (item["name"] + ".call.json"),
            dict(
                trajectory=item,
                source_sha256=sha(source),
                started_utc=now(),
                ordinal=calls + 1,
            ),
        )
        calls += 1
        recorded = integrate(library, inputs_from_saved(saved, ctx.length))
        steps += len(recorded["previous"])
        np.savez_compressed(
            out / (item["name"] + ".npz"),
            **recorded,
            theta=saved["theta"],
            length=ctx.length,
            observation_matrix=ctx.obs,
            y0=y0,
            dates=saved["dates"],
        )
        report = audit_trace(
            recorded, saved, ctx.length, ctx.obs, y0, spec["tolerances"]
        )
        seal(out / (item["name"] + ".audit.json"), report)
        print(
            json.dumps(
                dict(
                    trajectory=item["name"],
                    passed=report["passed"],
                    substeps=report["substeps"],
                )
            ),
            flush=True,
        )
    seal(
        out / "execution.json",
        dict(
            recorded_integrations=calls,
            substeps=steps,
            elapsed_seconds_before_replay=time.monotonic() - started,
            original_forward_calls=0,
            independent_integrations=0,
            parameter_fits=0,
            neural_updates=0,
        ),
    )
    checked = verify(out, sealed=False)
    seal(out / "verification.json", checked)
    if not checked["passed"]:
        raise ArithmeticError("Original trace audit failed; do not start PINN training")
    check_hashes(protected)
    check_hashes(sources)
    seal(
        out / "completed.json",
        dict(
            passed=True,
            completed_utc=now(),
            elapsed_seconds=time.monotonic() - started,
            **read_json(out / "execution.json"),
        ),
    )


def worker(out):
    started = time.monotonic()
    with (out / "run.log").open("x", buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                setup()
                execute(out, started)
            except BaseException as error:
                traceback.print_exc()
                seal(
                    out / "failed.json",
                    dict(
                        error=repr(error),
                        elapsed_seconds=time.monotonic() - started,
                        failed_utc=now(),
                    ),
                )
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", args.run_id):
        raise ValueError("Unsafe run id")
    out = ROOT / "results/ootang_bplus_v1_8" / args.run_id
    if args.verify:
        setup()
        print(json.dumps(verify(out), indent=2))
        return
    spec = specification()
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
                dict(
                    error="600 second hard timeout",
                    elapsed_seconds=time.monotonic() - started,
                    failed_utc=now(),
                ),
            )
    if process.exitcode != 0:
        if not (out / "failed.json").exists():
            seal(
                out / "failed.json",
                dict(error=f"Worker exited {process.exitcode}", failed_utc=now()),
            )
        index_artifacts(out)
        raise SystemExit(f"Audit stopped; evidence preserved in {out}")
    seal(
        out / "launcher.json",
        dict(exitcode=process.exitcode, wall_seconds=time.monotonic() - started),
    )
    index_artifacts(out)
    check_index(out)
    print(json.dumps(read_json(out / "completed.json"), indent=2))
    print(out)


if __name__ == "__main__":
    main()
