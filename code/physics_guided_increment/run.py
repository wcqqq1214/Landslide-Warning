"""Execute a fresh, bounded v1.3 run with immutable v1.2 controls."""

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
import subprocess
import sys
import time
import traceback
import numpy as np
import pandas as pd

from physics_guided.reference import ROOT, load, sha, save_json
from physics_guided.calibration import initial
from physics_guided_diagnostics.core import (
    read_prefix,
    choose_start,
    metric_rows,
    growth_rows,
    array_sha,
)
from physics_guided_diagnostics.run import (
    verify_reference,
    REFERENCE,
    protected_hashes as earlier_hashes,
    source_files as earlier_sources,
)
from .objective import specification, fit_stage, components, increment_rows


def source_files():
    return sorted(
        earlier_sources() + list((ROOT / "code/physics_guided_increment").glob("*.py"))
    )


def protected_hashes(control):
    files = [p for p in control.rglob("*") if p.is_file()]
    files += [
        ROOT / "docs" / f
        for f in [
            "ootang_bplus_diagnostics_plan.v1.2.md",
            "ootang_bplus_diagnostics_results.v1.2.md",
            "ootang_bplus_probabilistic_implementation.v1_1.md",
        ]
    ]
    return earlier_hashes() | {str(p.relative_to(ROOT)): sha(p) for p in files}


def train_chain(out_string, n, start):
    out = Path(out_string)
    scope = out / f"fit_{n}" / start
    scope.mkdir(parents=True, exist_ok=False)
    with (scope / "execution.log").open("w", buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            records = []
            try:
                ref = load(REFERENCE)
                drivers, labels, _ = read_prefix(out / "input_prefix_792.csv")
                theta = initial(ref, start)
                for number, (weight, budget) in enumerate(
                    zip([0, n, 100 * n, 100 * n], [900, 800, 800, 800]), 1
                ):
                    r = fit_stage(
                        ref,
                        drivers.prefix(n),
                        labels[:n].copy(),
                        n,
                        theta,
                        weight,
                        budget,
                        f"{n}/{start}/stage{number}",
                    )
                    records.append(r)
                    save_json(scope / f"stage{number}.json", r)
                    theta = np.array(r["theta"])
                status = dict(status="complete", fit_days=n, start=start)
            except Exception as exc:
                status = dict(
                    status="failed",
                    fit_days=n,
                    start=start,
                    error=repr(exc),
                    traceback=traceback.format_exc(),
                    stage=getattr(exc, "diagnostic_stage", None),
                )
            save_json(scope / "status.json", status)
            return dict(**status, records=records)


def evaluate(out, control, drivers, labels):
    from physics_guided.mechanics import Mechanics

    ref = load(REFERENCE)
    all_predictions = []
    all_metrics = []
    all_growth = []
    all_increments = []
    choices = {}
    for n in (432, 612):
        scope = out / f"fit_{n}"
        end = n + 180
        full = drivers.prefix(end)
        y = labels[:end]
        evaluated = {}
        for arm, stage in [("C0", "final"), ("C1", "stage3"), ("C1", "final")]:
            candidates = {}
            for start in ("A", "B"):
                name = f"{arm}_{start}_{stage}"
                try:
                    if arm == "C0":
                        record = json.loads(
                            (control / f"fit_{n}/{start}_continued.json").read_text()
                        )
                    else:
                        number = 3 if stage == "stage3" else 4
                        record = json.loads(
                            (scope / start / f"stage{number}.json").read_text()
                        )
                    theta = np.array(record["theta"])
                    mechanics = Mechanics(ref, theta, full)
                    mu = mechanics.u + full.y0
                    state = mechanics.reference
                    if not np.isfinite(mu).all():
                        raise ArithmeticError("Nonfinite prediction")
                    terms = components(mu[:n], y[:n], 100 * n)
                    objective = terms[
                        "original_objective" if arm == "C0" else "joint_objective"
                    ]
                    if abs(objective - record["objective"]) > 1e-7:
                        raise ArithmeticError("Independent objective mismatch")
                    if arm == "C0":
                        saved = np.load(control / f"fit_{n}/{start}_continued.npz")
                        if abs(saved["mean"] - mu).max() > 1e-8:
                            raise ArithmeticError("Frozen control prediction mismatch")
                    custom, _, checks = mechanics.zero_trajectory()
                    reconstructed = (
                        custom[:, :4] + custom[:, 20:24]
                    ) @ mechanics.ctx.obs.T + full.y0
                    difference = float(abs(reconstructed - mu).max())
                    short = ref.forward(theta, ref.Context(full.forcing[:n])) + full.y0
                    prefix_error = float(abs(short - mu[:n]).max())
                    if difference > 1e-5 or prefix_error > 1e-8:
                        raise ArithmeticError(
                            "Mechanical reproduction or prefix mismatch"
                        )
                    audit = dict(
                        valid=True,
                        objective=objective,
                        **terms,
                        zero_trajectory_max_error_mm=difference,
                        prefix_error_mm=prefix_error,
                        substeps_checked=64 * (end - 1),
                        min_x=float(checks[:, 0].min()),
                        min_w=float(checks[:, 1].min()),
                        max_normalized_complementarity=float(checks[:, 2].max()),
                        min_slip_tolerance_margin=float(checks[:, 3].min()),
                        active_set_changes=int(checks[:, 4].sum()),
                    )
                    candidates[start] = audit
                    save_json(scope / f"{name}_audit.json", audit)
                    np.savez_compressed(
                        scope / f"{name}.npz",
                        mean=mu,
                        theta=theta,
                        dates=full.dates.strftime("%Y-%m-%d").to_numpy(dtype="U10"),
                        observation_matrix=mechanics.ctx.obs,
                        substep_audit=checks,
                        **state,
                    )
                    for t, date in enumerate(full.dates):
                        for j, station in enumerate(["ATU1", "ATU5", "MJ3", "MJ1"]):
                            all_predictions.append(
                                dict(
                                    fit_days=n,
                                    candidate=name,
                                    date=str(date.date()),
                                    station=station,
                                    phase="warmup"
                                    if t < 30
                                    else ("train" if t < n else "prediction"),
                                    mean_mm=float(mu[t, j]),
                                    observed_mm=float(y[t, j]),
                                )
                            )
                    for destination, rows in [
                        (all_metrics, metric_rows(mu, y, n, name)),
                        (
                            all_growth,
                            growth_rows(full, y, mu, state, mechanics.ctx.obs, n, name),
                        ),
                        (all_increments, increment_rows(mu, y, n, name)),
                    ]:
                        destination.extend(dict(fit_days=n, **row) for row in rows)
                except Exception as exc:
                    candidates[start] = dict(
                        valid=False, error=repr(exc), traceback=traceback.format_exc()
                    )
                    save_json(scope / f"{name}_failed.json", candidates[start])
            evaluated[f"{arm}_{stage}"] = dict(
                candidates=candidates, selected_start=choose_start(candidates)
            )
            if arm == "C0":
                locked = json.loads((control / f"fit_{n}/selection.json").read_text())[
                    "continued"
                ]["selected_start"]
                if evaluated[f"{arm}_{stage}"]["selected_start"] != locked:
                    raise ArithmeticError("Cannot change frozen control selection")
        choices[str(n)] = evaluated
    save_json(out / "selection.json", choices)
    for name, rows in [
        ("predictions", all_predictions),
        ("metrics", all_metrics),
        ("growth", all_growth),
        ("increment_metrics", all_increments),
    ]:
        pd.DataFrame(rows).to_csv(out / f"{name}.csv", index=False)
    return choices


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not args.run_id or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for c in args.run_id
    ):
        raise ValueError("Simple unique run id required")
    spec = specification()
    config = ROOT / "config/ootang_bplus_increment.v1_3.json"
    if json.loads(config.read_text()) != spec:
        raise ValueError("Changed config requires a new version")
    control = ROOT / spec["control_run"]
    out = ROOT / "results/ootang_bplus_v1_3" / args.run_id
    if out.exists():
        raise FileExistsError("No automatic resume or overwrite")
    reference = verify_reference()
    old = json.loads((control / "manifest.json").read_text())
    for path, digest in old["sources"].items():
        if sha(ROOT / path) != digest:
            raise ValueError(f"Changed control dependency: {path}")
    index = json.loads((control / "artifact_manifest.json").read_text())["files"]
    for path, item in index.items():
        if sha(control / path) != item["sha256"]:
            raise ValueError(f"Changed control artifact: {path}")
    protected = protected_hashes(control)
    out.mkdir(parents=True)
    shutil.copy2(control / "input_prefix_792.csv", out / "input_prefix_792.csv")
    drivers, labels, _ = read_prefix(out / "input_prefix_792.csv")
    if (
        array_sha(labels) != old["runtime_prefix_labels_sha256"]
        or array_sha(drivers.forcing) != old["runtime_prefix_forcing_sha256"]
    ):
        raise ValueError("Control data identity mismatch")
    sources = {}
    for path in source_files():
        rel = path.relative_to(ROOT)
        destination = out / "source_snapshot" / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        sources[str(rel)] = sha(path)
    plan = ROOT / spec["plan"]
    shutil.copy2(plan, out / "plan_before_execution.md")
    shutil.copy2(config, out / "config.json")
    manifest = dict(
        specification=spec,
        git_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        started_utc=pd.Timestamp.now(tz="UTC").isoformat(),
        command=sys.argv,
        python=sys.version,
        platform=platform.platform(),
        dependencies={
            k: importlib.metadata.version(k)
            for k in ["numpy", "scipy", "pandas", "torch", "matplotlib"]
        },
        sources=sources,
        plan_sha256=sha(plan),
        config_sha256=sha(config),
        input_csv_sha256=sha(out / "input_prefix_792.csv"),
        runtime_prefix_labels_sha256=array_sha(labels),
        runtime_prefix_forcing_sha256=array_sha(drivers.forcing),
        control_manifest_sha256=sha(control / "artifact_manifest.json"),
        reference_provenance=reference,
        exploratory=True,
        user_acceptance="pending",
    )
    save_json(out / "manifest.json", manifest)
    save_json(out / "protected_before.json", protected)
    started = time.monotonic()
    chains = []
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=3, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        pending = {
            pool.submit(train_chain, str(out), n, s): (n, s)
            for n in [432, 612]
            for s in ["A", "B"]
        }
        while pending:
            done, _ = concurrent.futures.wait(
                pending, timeout=30, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                n, s = pending.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = dict(
                        fit_days=n,
                        start=s,
                        status="failed",
                        error=repr(exc),
                        records=[],
                    )
                chains.append(result)
                print({k: v for k, v in result.items() if k != "records"}, flush=True)
            print(
                f"elapsed={time.monotonic() - started:.0f}s remaining={list(pending.values())}",
                flush=True,
            )
    save_json(out / "fitted_parameters_locked.json", chains)
    choices = evaluate(out, control, drivers, labels)
    preservation = dict(
        protected_unchanged=protected == protected_hashes(control),
        protected_file_count=len(protected),
        source_unchanged=all(sha(ROOT / p) == h for p, h in sources.items()),
        plan_unchanged=sha(plan) == manifest["plan_sha256"],
        config_unchanged=sha(config) == manifest["config_sha256"],
    )
    save_json(out / "preservation.json", preservation)
    if not all(v for k, v in preservation.items() if k != "protected_file_count"):
        raise RuntimeError("Scientific input or source changed during execution")
    complete = all(r["status"] == "complete" for r in chains) and all(
        a["valid"]
        for fold in choices.values()
        for value in fold.values()
        for a in value["candidates"].values()
    )
    save_json(
        out / "completion.json",
        dict(
            status="complete" if complete else "partial",
            seconds=time.monotonic() - started,
            optimization_forward_calls=sum(
                r["optimization_forward_calls"] for c in chains for r in c["records"]
            ),
            new_optimizer_nfev=sum(r["nfev"] for c in chains for r in c["records"]),
            user_acceptance="pending",
        ),
    )
    print(f"Finished: {out}", flush=True)


if __name__ == "__main__":
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    main()
