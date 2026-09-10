"""Run the sealed v1.2 prefix diagnostics once, keeping v1.1 read-only."""

import argparse
import concurrent.futures
import contextlib
import importlib.metadata
import json
import multiprocessing
import os
from pathlib import Path
import platform
import shutil
import sys
import time
import traceback
from zipfile import ZipFile

import numpy as np
import pandas as pd

from physics_guided.reference import ROOT, ARCHIVE_PREFIX, load, sha, save_json
from physics_guided.calibration import initial
from physics_guided.data import POINTS
from .core import (
    read_prefix,
    fit_stage,
    choose_start,
    metric_rows,
    growth_rows,
    array_sha,
)

PRIOR = ROOT / "results/ootang_bplus_v1_1/20260910_implementation"
REFERENCE = ROOT / "runtime/ootang_bplus_v1_1/reference"
PLAN = ROOT / "docs/ootang_bplus_diagnostics_plan.v1.2.md"


def protected_hashes():
    files = [
        ROOT / "manuscript.pdf",
        ROOT / "藕塘滑坡_二维模型B加_优化预测报告.pdf",
        ROOT / "section2d_v4.zip",
        ROOT / "docs/ootang_bplus_probabilistic_experiment_plan.v1.1.md",
    ]
    files += [p for p in PRIOR.rglob("*") if p.is_file()]
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(files)}


def source_files():
    files = list((ROOT / "code/physics_guided_diagnostics").glob("*.py"))
    files += [
        ROOT / "code/physics_guided" / f
        for f in [
            "__init__.py",
            "reference.py",
            "data.py",
            "calibration.py",
            "mechanics.py",
            "mechanics.c",
        ]
    ]
    return sorted(files)


def verify_reference():
    provenance = json.loads((REFERENCE / "provenance.json").read_text())
    if sha(ROOT / "section2d_v4.zip") != provenance["archive_sha256"]:
        raise ValueError("Source archive changed")
    with ZipFile(ROOT / "section2d_v4.zip") as archive:
        for path, expected in provenance["source_hashes"].items():
            if sha(REFERENCE / path) != expected:
                raise ValueError(f"Extracted frozen source changed: {path}")
            if archive.read(ARCHIVE_PREFIX + path) != (REFERENCE / path).read_bytes():
                raise ValueError(f"Archive identity mismatch: {path}")
    adapted = REFERENCE / "section2d_v4/physical_model_posix.py"
    if sha(adapted) != provenance["adapted_python_sha256"]:
        raise ValueError("Adapted reference changed")
    suffix = ".dylib" if sys.platform == "darwin" else ".so"
    if (
        sha(REFERENCE / f"section2d_v4/physical_solver{suffix}")
        != provenance["library_sha256"]
    ):
        raise ValueError("Native library changed")
    lock = json.loads((PRIOR / "selection.json").read_text())
    for path in source_files():
        key = str(path.relative_to(ROOT))
        if key in lock["sources"] and sha(path) != lock["sources"][key]:
            raise ValueError(f"Frozen dependency changed: {key}")
    return provenance


def audit_prediction(ref, theta, drivers, labels, n, record, output, name):
    from physics_guided.mechanics import Mechanics

    mechanics = Mechanics(ref, theta, drivers)
    mu, state = mechanics.u + drivers.y0, mechanics.reference
    custom, _, checks = mechanics.zero_trajectory()
    mapped = (custom[:, :4] + custom[:, 20:24]) @ mechanics.ctx.obs.T + drivers.y0
    short = ref.forward(theta, ref.Context(drivers.forcing[:n])) + drivers.y0
    difference = float(abs(mapped - mu).max())
    prefix_error = float(abs(short - mu[:n]).max())
    residual = (mu[:n] - labels[:n]) / 100
    objective = float(
        (residual**2).sum() + record["lambda_T"] * (residual[-1] ** 2).sum()
    )
    if (
        difference > 1e-5
        or prefix_error > 1e-8
        or abs(objective - record["objective"]) > 1e-7
    ):
        raise ArithmeticError("Candidate reproduction or objective mismatch")
    audit = dict(
        valid=True,
        objective=objective,
        zero_trajectory_max_error_mm=difference,
        prefix_error_mm=prefix_error,
        substeps_checked=64 * (len(mu) - 1),
        min_x=float(checks[:, 0].min()),
        min_w=float(checks[:, 1].min()),
        max_normalized_complementarity=float(checks[:, 2].max()),
        min_slip_tolerance_margin=float(checks[:, 3].min()),
        active_set_changes=int(checks[:, 4].sum()),
        near_bound_indices=np.flatnonzero(
            np.minimum(
                (theta - ref.LO) / (ref.HI - ref.LO),
                (ref.HI - theta) / (ref.HI - ref.LO),
            )
            < 0.01
        ).tolist(),
    )
    np.savez_compressed(
        output / f"{name}.npz",
        dates=drivers.dates.strftime("%Y-%m-%d").to_numpy(dtype="U10"),
        mean=mu,
        theta=theta,
        observation_matrix=mechanics.ctx.obs,
        substep_audit=checks,
        **state,
    )
    save_json(output / f"{name}_audit.json", audit)
    rows = []
    for t, date in enumerate(drivers.dates):
        for j, station in enumerate(POINTS):
            rows.append(
                dict(
                    candidate=name,
                    date=str(date.date()),
                    station=station,
                    phase="warmup" if t < 30 else ("train" if t < n else "prediction"),
                    mean_mm=float(mu[t, j]),
                    observed_mm=float(labels[t, j]),
                )
            )
    return (
        audit,
        rows,
        metric_rows(mu, labels, n, name),
        growth_rows(drivers, labels, mu, state, mechanics.ctx.obs, n, name),
    )


def run_scope(out_string, n):
    out = Path(out_string)
    scope = out / f"fit_{n}"
    scope.mkdir(exist_ok=False)
    with (scope / "execution.log").open("w", buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                result = scope_inner(out, scope, n)
            except Exception as exc:
                result = dict(
                    fit_days=n,
                    status="failed",
                    failure=repr(exc),
                    traceback=traceback.format_exc(),
                )
            save_json(scope / "status.json", result)
            return result


def scope_inner(out, scope, n):
    ref = load(REFERENCE)
    drivers, labels, _ = read_prefix(out / "input_prefix_792.csv")
    end = n + 180 if n < 792 else n
    train = drivers.prefix(n)
    records = {}
    for start in ("A", "B"):
        records[start] = {}
        try:
            if n == 792:
                old = json.loads((out / f"prior_calibration_{start}.json").read_text())
                stage = dict(
                    old["stages"][-1],
                    source="v1.1 existing prefix; no optimization repeated",
                )
                stage["fit_days"] = n
                records[start]["baseline"] = stage
                theta = np.array(stage["theta"])
            else:
                theta = initial(ref, start)
                for number, (weight, budget) in enumerate(
                    ((0, 900), (n, 800), (100 * n, 800)), 1
                ):
                    stage = fit_stage(
                        ref,
                        train,
                        labels[:n].copy(),
                        n,
                        theta,
                        weight,
                        budget,
                        f"{n}/{start}/stage{number}",
                    )
                    theta = np.array(stage["theta"])
                    save_json(scope / f"{start}_stage{number}.json", stage)
                records[start]["baseline"] = stage
            save_json(scope / f"{start}_baseline.json", records[start]["baseline"])
            continuation = fit_stage(
                ref,
                train,
                labels[:n].copy(),
                n,
                theta,
                100 * n,
                800,
                f"{n}/{start}/continuation",
            )
            records[start]["continued"] = continuation
            save_json(scope / f"{start}_continued.json", continuation)
        except Exception as exc:
            records[start]["failure"] = dict(
                error=repr(exc),
                traceback=traceback.format_exc(),
                stage=getattr(exc, "diagnostic_stage", None),
            )
            save_json(scope / f"{start}_failure.json", records[start]["failure"])
    # This file seals all fitted parameters before any held-out observation is scored.
    save_json(scope / "fitted_parameters_locked.json", records)
    forecasts, metrics, growth = [], [], []
    selection = {}
    for variant in ("baseline", "continued"):
        evaluated = {}
        for start in ("A", "B"):
            if variant not in records[start]:
                evaluated[start] = dict(valid=False, reason="fit not available")
                continue
            record = records[start][variant]
            name = f"{start}_{variant}"
            try:
                audit, pred, score, rate = audit_prediction(
                    ref,
                    np.array(record["theta"]),
                    drivers.prefix(end),
                    labels[:end],
                    n,
                    record,
                    scope,
                    name,
                )
                evaluated[start] = audit
                forecasts.extend(pred)
                metrics.extend(score)
                growth.extend(rate)
            except Exception as exc:
                evaluated[start] = dict(
                    valid=False, reason=repr(exc), traceback=traceback.format_exc()
                )
                save_json(scope / f"{name}_audit_failed.json", evaluated[start])
        selection[variant] = dict(
            selected_start=choose_start(evaluated),
            rule="training objective only; tie <=1e-12 chooses A",
            candidates=evaluated,
        )
    pd.DataFrame(forecasts).to_csv(scope / "predictions.csv", index=False)
    pd.DataFrame(metrics).to_csv(scope / "metrics.csv", index=False)
    pd.DataFrame(growth).to_csv(scope / "growth.csv", index=False)
    save_json(scope / "selection.json", selection)
    complete = all(
        selection[v]["candidates"][s].get("valid", False)
        for v in ("baseline", "continued")
        for s in ("A", "B")
    )
    return dict(
        fit_days=n,
        prediction_days=end - n,
        status="complete" if complete else "partial",
        selection={v: r["selected_start"] for v, r in selection.items()},
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not args.run_id or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for c in args.run_id
    ):
        raise ValueError("Simple unique run id required")
    out = ROOT / "results/ootang_bplus_v1_2" / args.run_id
    if out.exists():
        raise FileExistsError("No automatic resume or overwrite; choose a new run id")
    provenance = verify_reference()
    drivers, labels, frame = read_prefix(ROOT / "data/monitoring_data.csv")
    protected = protected_hashes()
    out.mkdir(parents=True)
    frame.to_csv(out / "input_prefix_792.csv", index=False)
    # Check the CSV round trip before optimizing; preserve exact in-memory source checksums too.
    copy_drivers, copy_labels, _ = read_prefix(out / "input_prefix_792.csv")
    if not np.allclose(labels, copy_labels, atol=1e-10, rtol=0):
        raise ValueError("Prefix copy precision loss")
    for start in ("A", "B"):
        shutil.copy2(
            PRIOR / f"calibration_{start}.json", out / f"prior_calibration_{start}.json"
        )
    shutil.copy2(PLAN, out / "plan_before_execution.md")
    sources = {}
    for path in source_files():
        relative = path.relative_to(ROOT)
        target = out / "source_snapshot" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        sources[str(relative)] = sha(path)
    manifest = dict(
        run_id=args.run_id,
        started_utc=pd.Timestamp.now(tz="UTC").isoformat(),
        plan_sha256=sha(PLAN),
        sources=sources,
        command=sys.argv,
        python=sys.version,
        platform=platform.platform(),
        dependencies={
            k: importlib.metadata.version(k)
            for k in ["numpy", "scipy", "pandas", "torch"]
        },
        fitting_lengths=[432, 612, 792],
        prediction_lengths=[180, 180, 0],
        original_prefix_labels_sha256=array_sha(labels),
        runtime_prefix_labels_sha256=array_sha(copy_labels),
        original_prefix_forcing_sha256=array_sha(drivers.forcing),
        runtime_prefix_forcing_sha256=array_sha(copy_drivers.forcing),
        input_csv_sha256=sha(out / "input_prefix_792.csv"),
        prefix_last_date=str(drivers.dates[-1].date()),
        exploratory=True,
        max_total_nfev=14800,
        model_training="none",
        user_acceptance="pending",
        reference_provenance=provenance,
    )
    save_json(out / "manifest.json", manifest)
    save_json(out / "protected_before.json", protected)
    started = time.monotonic()
    results = []
    # Spawn isolated numerical processes, not research agents; no label sharing between optimizers.
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=3, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        pending = {pool.submit(run_scope, str(out), n): n for n in [432, 612, 792]}
        while pending:
            done, _ = concurrent.futures.wait(
                pending, timeout=30, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                n = pending.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = dict(fit_days=n, status="failed", failure=repr(exc))
                results.append(result)
                print(json.dumps(result), flush=True)
            print(
                f"elapsed={time.monotonic() - started:.0f}s remaining={list(pending.values())}",
                flush=True,
            )
    after = protected_hashes()
    sources_unchanged = all(
        sha(ROOT / path) == digest for path, digest in sources.items()
    )
    save_json(
        out / "preservation.json",
        dict(
            protected_unchanged=protected == after,
            source_unchanged=sources_unchanged,
            plan_unchanged=sha(PLAN) == manifest["plan_sha256"],
            protected_file_count=len(protected),
        ),
    )
    save_json(
        out / "completion.json",
        dict(
            scopes=results,
            seconds=time.monotonic() - started,
            status="complete"
            if all(r["status"] == "complete" for r in results)
            else "partial",
            user_acceptance="pending",
        ),
    )
    if protected != after or not sources_unchanged:
        raise RuntimeError(
            "Protected input or scientific code changed during execution"
        )
    print(f"Finished: {out}", flush=True)


if __name__ == "__main__":
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    main()
