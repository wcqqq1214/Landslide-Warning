"""Run one v1.4 task once. Task B references completed task A for total timeout."""

import argparse
import contextlib
import importlib.metadata
import json
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
from physics_guided.data import POINTS
from physics_guided_diagnostics.core import (
    array_sha,
    choose_start,
    metric_rows,
    bounded_jacobian,
)
from physics_guided_diagnostics.run import REFERENCE, verify_reference
from physics_guided_increment.objective import components, residual
from physics_guided_increment.run import source_files as old_sources
from .core import (
    INNER,
    read_input,
    validate_source,
    fit_stage,
    choose_anchor,
    retain_incumbent,
    inner_scores,
    select_recipe,
)

BASE = ROOT / "results/ootang_bplus_v1_4"
C0 = ROOT / "results/ootang_bplus_v1_2/20260910_diagnostics"
C1 = ROOT / "results/ootang_bplus_v1_3/20260910_increment"
REVIEW = BASE / "20260910_optimization_review"
CONFIG = ROOT / "config/ootang_bplus_optimization_selection.v1_4.json"
CONFIG_SHA = "c4f34eaf46402e790f07df61b3a0c52e966c65208a2962fdce94ce36f7f016d4"
PLAN_SHA = "6aa5080636390ed819eed6e1bfca3d892b29b1d9ca7489d19a0c2f63025fbeb8"


def read_json(path):
    return json.loads(Path(path).read_text())


def seal(path, value):
    """Exclusive creation; a lock can never be rewritten by this runner."""
    with Path(path).open("x") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")


def index_artifacts(out):
    index = out / "artifact_manifest.json"
    seal(
        index,
        dict(
            files={
                str(p.relative_to(out)): dict(sha256=sha(p), bytes=p.stat().st_size)
                for p in sorted(out.rglob("*"))
                if p.is_file() and p != index
            }
        ),
    )


def verify_index(out):
    for path, info in read_json(out / "artifact_manifest.json")["files"].items():
        if sha(out / path) != info["sha256"]:
            raise ValueError(f"Changed source artifact: {out / path}")


def protected_hashes():
    old = read_json(REVIEW / "protected_before.json")
    old.update(
        {str(p.relative_to(ROOT)): sha(p) for p in REVIEW.rglob("*") if p.is_file()}
    )
    for p in [
        CONFIG,
        ROOT / "docs/ootang_bplus_optimization_selection_plan.v1.4.md",
        ROOT / "docs/ootang_bplus_optimization_review.v1.4.md",
    ]:
        old[str(p.relative_to(ROOT))] = sha(p)
    return old


def load_prior(n, recipe, version, drivers, labels, ref):
    path = (
        C0 / f"fit_{n}/{recipe}_continued.json"
        if version == "1.2"
        else C1 / f"fit_{n}/{recipe}/stage4.json"
    )
    record = read_json(path)
    theta = validate_source(record, drivers, labels, n, ref)
    return dict(
        record, source=str(path.relative_to(ROOT)), source_sha256=sha(path)
    ), theta


def audit_trajectory(ref, theta, drivers, output):
    """Driving series only; enforce original constraints at every substep."""
    from physics_guided.mechanics import Mechanics

    mechanics = Mechanics(ref, theta, drivers)
    mu = mechanics.u + drivers.y0
    custom, _, checks = mechanics.zero_trajectory()
    reconstructed = (
        custom[:, :4] + custom[:, 20:24]
    ) @ mechanics.ctx.obs.T + drivers.y0
    difference = float(abs(reconstructed - mu).max())
    if not np.isfinite(mu).all() or difference > 1e-5:
        raise ArithmeticError("Physical reproduction failed")
    audit = dict(
        valid=True,
        forward_calls=1,
        custom_trajectory_calls=1,
        zero_trajectory_max_error_mm=difference,
        substeps_checked=64 * (len(mu) - 1),
        min_x=float(checks[:, 0].min()),
        min_w=float(checks[:, 1].min()),
        max_normalized_complementarity=float(checks[:, 2].max()),
        min_slip_tolerance_margin=float(checks[:, 3].min()),
        active_set_changes=int(checks[:, 4].sum()),
    )
    np.savez_compressed(
        output,
        mean=mu,
        theta=theta,
        dates=drivers.dates.strftime("%Y-%m-%d").to_numpy(dtype="U10"),
        observation_matrix=mechanics.ctx.obs,
        substep_audit=checks,
        **mechanics.reference,
    )
    save_json(output.with_suffix(".audit.json"), audit)
    return mu, audit


def local_gradient(ref, theta, drivers, labels):
    from scipy.optimize._lsq.common import CL_scaling_vector

    ctx, target = ref.Context(drivers.forcing), labels - drivers.y0

    def fun(x):
        return residual(ref.forward(x, ctx), target, 100 * len(labels))

    r = fun(theta)
    jac = bounded_jacobian(fun, theta, ref.HI)
    g = np.einsum("ij,i->j", jac, r)
    v, _ = CL_scaling_vector(theta, g, ref.LO, ref.HI)
    return dict(
        objective_gradient="J1/2, Coleman-Li scaled",
        forward_calls=56,
        optimality=float(np.max(abs(g * v))),
        column_l2=np.linalg.norm(jac, axis=0).tolist(),
        gradient=g.tolist(),
    )


def optimization_job(out, n):
    scope = out / f"A_{n}"
    scope.mkdir()
    ref = load(REFERENCE)
    drivers, labels = read_input(out / "input_prefix_792.csv", n)
    pool = {}
    for version in ("1.2", "1.3"):
        for recipe in ("A", "B"):
            name = f"v{version}_{recipe}"
            try:
                record, theta = load_prior(n, recipe, version, drivers, labels, ref)
                mu, audit = audit_trajectory(ref, theta, drivers, scope / f"{name}.npz")
                terms = components(mu, labels, 100 * n)
                old_key = (
                    "original_objective" if version == "1.2" else "joint_objective"
                )
                if abs(terms[old_key] - record["objective"]) > 1e-7:
                    raise ArithmeticError("Saved objective mismatch")
                pool[name] = dict(
                    valid=True,
                    theta=theta.tolist(),
                    objective=terms["joint_objective"],
                    source_record=record,
                    audit=audit,
                    **terms,
                )
            except Exception as exc:
                pool[name] = dict(
                    valid=False, error=repr(exc), traceback=traceback.format_exc()
                )
    selected = choose_anchor(pool)
    seal(scope / "anchor_locked.json", dict(selected=selected, candidates=pool))
    if selected is None:
        raise RuntimeError("No valid same-prefix anchor")
    anchor = pool[selected]
    attempted = None
    try:
        r = fit_stage(
            ref,
            drivers,
            labels,
            n,
            np.array(anchor["theta"]),
            "A",
            1,
            lambda state: save_json(out / f"A_{n}.progress.json", state),
        )
        save_json(scope / "stage1.json", r)
        mu, audit = audit_trajectory(
            ref, np.array(r["theta"]), drivers, scope / "attempt.npz"
        )
        objective = components(mu, labels, 100 * n)["joint_objective"]
        if abs(objective - r["objective"]) > 1e-7:
            raise ArithmeticError("Attempt objective mismatch")
        attempted = dict(valid=True, objective=objective, theta=r["theta"], audit=audit)
    except Exception as exc:
        save_json(
            scope / "attempt_failure.json",
            dict(
                error=repr(exc),
                traceback=traceback.format_exc(),
                stage=getattr(exc, "diagnostic_stage", None),
            ),
        )
    retained, reason = retain_incumbent(anchor, attempted)
    final = attempted if retained == "attempt" else anchor
    result = dict(
        fit_days=n,
        selected_anchor=selected,
        retained=retained,
        reason=reason,
        anchor_objective=anchor["objective"],
        retained_objective=final["objective"],
        objective_reduction=anchor["objective"] - final["objective"],
        theta=final["theta"],
        attempt_valid=attempted is not None,
    )
    seal(scope / "retained_locked.json", result)
    save_json(
        scope / "retained_gradient.json",
        local_gradient(ref, np.array(final["theta"]), drivers, labels),
    )
    return result


def selection_job(out, n, recipe):
    scope = out / f"B_{n}_{recipe}"
    scope.mkdir()
    ref = load(REFERENCE)
    drivers, labels = read_input(out / "input_prefix_792.csv", n)
    theta = initial(ref, recipe)
    for stage in range(1, 5):
        r = fit_stage(
            ref,
            drivers,
            labels,
            n,
            theta,
            "B",
            stage,
            lambda state: save_json(out / f"B_{n}_{recipe}.progress.json", state),
        )
        save_json(scope / f"stage{stage}.json", r)
        theta = np.array(r["theta"])
    return dict(fit_days=n, recipe=recipe, objective=r["objective"])


def worker(out, job):
    with (out / f"{job}.log").open("x", buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                tokens = job.split("_")
                result = (
                    optimization_job(out, int(tokens[1]))
                    if tokens[0] == "A"
                    else selection_job(out, int(tokens[1]), tokens[2])
                )
                result = dict(status="complete", result=result)
            except Exception as exc:
                result = dict(
                    status="failed",
                    error=repr(exc),
                    traceback=traceback.format_exc(),
                    stage=getattr(exc, "diagnostic_stage", None),
                )
            save_json(out / f"{job}.status.json", result)


def monitor_jobs(out, jobs, timeout):
    """Bounded subprocesses, durable partial accounting, no automatic retry."""
    start = time.monotonic()
    active, pending, finished, samples = {}, list(jobs), {}, []
    changed = {}
    with (out / "monitor.log").open("x", buffering=1) as log:
        while active or pending:
            if time.monotonic() - start >= timeout:
                print("Hard timeout: stopping remaining jobs; no restart", flush=True)
                log.write("hard timeout; preserving partial records\n")
                for job, process in active.items():
                    process.kill()
                    process.wait()
                    finished[job] = dict(
                        status="timeout", returncode=process.returncode
                    )
                finished.update(
                    {job: dict(status="not_started_timeout") for job in pending}
                )
                break
            while pending and len(active) < 3:
                job = pending.pop(0)
                env = dict(os.environ, PYTHONPATH=str(ROOT / "code"))
                env.update(
                    {
                        key: "1"
                        for key in (
                            "OMP_NUM_THREADS",
                            "OPENBLAS_NUM_THREADS",
                            "MKL_NUM_THREADS",
                            "VECLIB_MAXIMUM_THREADS",
                            "NUMEXPR_NUM_THREADS",
                        )
                    }
                )
                cmd = [
                    sys.executable,
                    "-m",
                    "physics_guided_optimization_selection.run",
                    "--worker",
                    job,
                    "--worker-output",
                    str(out),
                ]
                active[job] = subprocess.Popen(
                    cmd, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=log
                )
                changed[job] = (0, time.monotonic())
            for job, process in list(active.items()):
                if process.poll() is not None:
                    path = out / f"{job}.status.json"
                    status = (
                        read_json(path) if path.exists() else dict(status="crashed")
                    )
                    finished[job] = dict(status, returncode=process.returncode)
                    del active[job]
                    print(job, finished[job]["status"], flush=True)
            elapsed = time.monotonic() - start
            snapshot = dict(elapsed_seconds=elapsed, active={}, pending=pending.copy())
            for job, process in active.items():
                path = out / f"{job}.progress.json"
                size = path.stat().st_mtime_ns if path.exists() else 0
                last_size, last_change = changed[job]
                if size != last_size:
                    last_change = time.monotonic()
                    changed[job] = (size, last_change)
                snapshot["active"][job] = dict(
                    pid=process.pid, stall_advisory=time.monotonic() - last_change >= 90
                )
            samples.append(snapshot)
            log.write(json.dumps(snapshot) + "\n")
            save_json(
                out / "monitor_progress.json", dict(samples=samples, finished=finished)
            )
            print(
                f"elapsed={elapsed:.0f}s active={list(active)} pending={pending}",
                flush=True,
            )
            if active:
                time.sleep(min(30, max(0.01, timeout - elapsed)))
    return finished, time.monotonic() - start


def lock_and_evaluate(out):
    ref = load(REFERENCE)
    records = {}
    for n in (252, 342, 432, 612):
        drivers, labels = read_input(out / "input_prefix_792.csv", n)
        for recipe in ("A", "B"):
            key = f"{n}_{recipe}"
            try:
                if n < 432:
                    path = out / f"B_{n}_{recipe}/stage4.json"
                    r = read_json(path)
                    r = dict(
                        r, source=str(path.relative_to(ROOT)), source_sha256=sha(path)
                    )
                    validate_source(r, drivers, labels, n, ref)
                else:
                    r, _ = load_prior(n, recipe, "1.2", drivers, labels, ref)
                records[key] = dict(valid=True, record=r)
            except Exception as exc:
                records[key] = dict(valid=False, error=repr(exc))
    seal(
        out / "fitted_parameters_locked.json",
        dict(locked_utc=pd.Timestamp.now(tz="UTC").isoformat(), records=records),
    )
    # Construct each selector from that outer prefix only. No outer labels in this loop.
    selections, all_windows = {}, {}
    for outer_n, internal in INNER.items():
        drivers, labels = read_input(out / "input_prefix_792.csv", outer_n)
        windows, training = {}, {}
        for n in internal:
            windows[n] = {}
            for recipe in ("A", "B"):
                r = records[f"{n}_{recipe}"]
                try:
                    if not r["valid"]:
                        raise ValueError(r["error"])
                    theta = np.array(r["record"]["theta"])
                    file = out / f"inner_{outer_n}_{n}_{recipe}.npz"
                    mu, _ = audit_trajectory(ref, theta, drivers.prefix(n + 180), file)
                    terms = components(mu[:n], labels[:n], 100 * n)
                    if (
                        abs(terms["original_objective"] - r["record"]["objective"])
                        > 1e-7
                    ):
                        raise ArithmeticError("Inner training objective mismatch")
                    windows[n][recipe] = inner_scores(
                        mu[n:], labels[n : n + 180], n, outer_n
                    )
                except Exception as exc:
                    windows[n][recipe] = dict(valid=False, error=repr(exc))
        for recipe in ("A", "B"):
            r = records[f"{outer_n}_{recipe}"]["record"]
            theta = np.array(r["theta"])
            mu, _ = audit_trajectory(
                ref, theta, drivers, out / f"train_{outer_n}_{recipe}.npz"
            )
            j0 = components(mu, labels, 100 * outer_n)["original_objective"]
            if abs(j0 - r["objective"]) > 1e-7:
                raise ArithmeticError("Outer training objective mismatch")
            training[recipe] = dict(valid=True, objective=j0)
        t = choose_start(training)
        old_t = read_json(C0 / f"fit_{outer_n}/selection.json")["continued"][
            "selected_start"
        ]
        if t != old_t:
            raise ArithmeticError("Frozen T selector changed")
        selections[str(outer_n)] = dict(
            T=t, V=select_recipe(outer_n, windows), training=training
        )
        all_windows[str(outer_n)] = windows
    seal(out / "inner_scores_locked.json", all_windows)
    seal(
        out / "selection_locked.json",
        dict(
            locked_utc=pd.Timestamp.now(tz="UTC").isoformat(),
            parameter_lock_sha256=sha(out / "fitted_parameters_locked.json"),
            inner_scores_sha256=sha(out / "inner_scores_locked.json"),
            selections=selections,
        ),
    )
    # Only after both selection locks exist, load outer observations and compute scores.
    predictions, metrics, failures = [], [], []
    for n in INNER:
        drivers, labels = read_input(out / "input_prefix_792.csv", n + 180)
        for recipe in ("A", "B"):
            try:
                theta = np.array(records[f"{n}_{recipe}"]["record"]["theta"])
                mu, _ = audit_trajectory(
                    ref, theta, drivers, out / f"outer_{n}_{recipe}.npz"
                )
                train_mu = np.load(out / f"train_{n}_{recipe}.npz")["mean"]
                prefix_error = float(abs(mu[:n] - train_mu).max())
                old_mu = np.load(C0 / f"fit_{n}/{recipe}_continued.npz")["mean"]
                frozen_error = float(abs(mu - old_mu).max())
                if prefix_error > 1e-8 or frozen_error > 1e-8:
                    raise ArithmeticError("Prefix consistency or frozen curve mismatch")
                save_json(
                    out / f"outer_{n}_{recipe}.comparison.json",
                    dict(
                        prefix_max_error_mm=prefix_error,
                        frozen_max_error_mm=frozen_error,
                    ),
                )
                metrics.extend(
                    dict(fit_days=n, **r) for r in metric_rows(mu, labels, n, recipe)
                )
                for t, date in enumerate(drivers.dates):
                    for j, point in enumerate(POINTS):
                        predictions.append(
                            dict(
                                fit_days=n,
                                recipe=recipe,
                                station=point,
                                date=str(date.date()),
                                mean_mm=float(mu[t, j]),
                                observed_mm=float(labels[t, j]),
                                phase="warmup"
                                if t < 30
                                else ("train" if t < n else "prediction"),
                            )
                        )
            except Exception as exc:
                failures.append(dict(fit_days=n, recipe=recipe, error=repr(exc)))
    pd.DataFrame(predictions).to_csv(out / "predictions.csv", index=False)
    pd.DataFrame(metrics).to_csv(out / "metrics.csv", index=False)
    seal(
        out / "outer_scoring_completed.json",
        dict(
            completed_utc=pd.Timestamp.now(tz="UTC").isoformat(),
            selection_lock_sha256=sha(out / "selection_locked.json"),
            failures=failures,
        ),
    )
    return selections


def prepare(out, task, optimization_run):
    if out.exists():
        raise FileExistsError("No overwrite or automatic resume")
    if sha(CONFIG) != CONFIG_SHA:
        raise ValueError("Changed config requires another version")
    config = read_json(CONFIG)
    plan = ROOT / config["plan"]
    if sha(plan) != PLAN_SHA:
        raise ValueError("Frozen v1.4 plan changed")
    reference = verify_reference()
    for folder in (C0, C1, REVIEW):
        verify_index(folder)
    for path, digest in read_json(C1 / "manifest.json")["sources"].items():
        if sha(ROOT / path) != digest:
            raise ValueError(f"Frozen scientific dependency changed: {path}")
    protected = protected_hashes()
    for path, digest in protected.items():
        if sha(ROOT / path) != digest:
            raise ValueError(f"Historical evidence changed: {path}")
    prior_seconds, prior_nfev = 0, 0
    if task == "B":
        prior = BASE / optimization_run
        verify_index(prior)
        a = read_json(prior / "completion.json")
        if (
            read_json(prior / "manifest.json")["task"] != "A"
            or a["status"] != "complete"
        ):
            raise ValueError("Task B requires a completed task A run")
        prior_seconds, prior_nfev = a["fitting_seconds"], a["new_optimizer_nfev"]
        protected.update(
            {str(p.relative_to(ROOT)): sha(p) for p in prior.rglob("*") if p.is_file()}
        )
    if prior_seconds >= 1800:
        raise RuntimeError("Total fitting timeout already exhausted")
    out.mkdir(parents=True)
    shutil.copy2(C0 / "input_prefix_792.csv", out / "input_prefix_792.csv")
    drivers, labels = read_input(out / "input_prefix_792.csv", 792)
    old = read_json(C0 / "manifest.json")
    if (
        array_sha(labels) != old["runtime_prefix_labels_sha256"]
        or array_sha(drivers.forcing) != old["runtime_prefix_forcing_sha256"]
    ):
        raise ValueError("Frozen input identity mismatch")
    sources = {}
    files = old_sources() + list(
        (ROOT / "code/physics_guided_optimization_selection").glob("*.py")
    )
    files += [ROOT / "tests/test_physics_guided_optimization_selection.py"]
    for path in files:
        rel = path.relative_to(ROOT)
        destination = out / "source_snapshot" / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        sources[str(rel)] = sha(path)
    shutil.copy2(plan, out / "plan_before_execution.md")
    shutil.copy2(CONFIG, out / "config.json")
    manifest = dict(
        task=task,
        specification=config,
        started_utc=pd.Timestamp.now(tz="UTC").isoformat(),
        git_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        command=sys.argv,
        python=sys.version,
        platform=platform.platform(),
        dependencies={
            k: importlib.metadata.version(k)
            for k in ("numpy", "scipy", "pandas", "torch", "matplotlib")
        },
        plan_sha256=sha(plan),
        config_sha256=sha(CONFIG),
        sources=sources,
        input_csv_sha256=sha(out / "input_prefix_792.csv"),
        reference_provenance=reference,
        earlier_optimization_run=optimization_run,
        earlier_fitting_seconds=prior_seconds,
        earlier_nfev=prior_nfev,
        fitting_timeout_seconds=1800 - prior_seconds,
        exploratory=True,
        neural_training=False,
        user_acceptance="pending",
    )
    seal(out / "manifest.json", manifest)
    seal(out / "protected_before.json", protected)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id")
    parser.add_argument("--task", choices=("A", "B"), default="A")
    parser.add_argument("--optimization-run")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker_output, args.worker)
        return
    for name in (args.run_id, args.optimization_run):
        if name is not None and (
            not name
            or any(
                c
                not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                for c in name
            )
        ):
            raise ValueError("Simple unique run id required")
    if not args.run_id or (args.task == "B" and not args.optimization_run):
        parser.error("run id required; task B also requires --optimization-run")
    out = BASE / args.run_id
    manifest = prepare(out, args.task, args.optimization_run)
    jobs = (
        [f"A_{n}" for n in INNER]
        if args.task == "A"
        else [f"B_{n}_{r}" for n in (252, 342) for r in ("A", "B")]
    )
    statuses, seconds = monitor_jobs(out, jobs, manifest["fitting_timeout_seconds"])
    seal(out / "jobs_completed.json", statuses)
    evaluation_error = None
    if args.task == "B":
        try:
            lock_and_evaluate(out)
        except Exception as exc:
            evaluation_error = dict(error=repr(exc), traceback=traceback.format_exc())
            save_json(out / "evaluation_failed.json", evaluation_error)
    records = [read_json(p) for p in out.glob("[AB]_*/stage*.json")]
    # Include the most recent failed/interrupted stage counters without double-counting completed stages.
    incomplete = []
    for path in out.glob("*.progress.json"):
        r = read_json(path)
        job = path.name.removesuffix(".progress.json")
        if not (out / job / f"stage{r['stage']}.json").exists():
            incomplete.append(r)
    nfev = sum(r["nfev"] for r in records + incomplete)
    calls = sum(r["optimization_forward_calls"] for r in records + incomplete)
    cap = 1600 if args.task == "A" else 13200
    if nfev > cap or calls > 56 * cap or nfev + manifest["earlier_nfev"] > 14800:
        raise RuntimeError("Prescribed budget exceeded")
    preservation = dict(
        protected_file_count=len(read_json(out / "protected_before.json")),
        protected_unchanged=all(
            sha(ROOT / p) == h
            for p, h in read_json(out / "protected_before.json").items()
        ),
        source_unchanged=all(
            sha(ROOT / p) == h for p, h in manifest["sources"].items()
        ),
    )
    save_json(out / "preservation.json", preservation)
    if not preservation["protected_unchanged"] or not preservation["source_unchanged"]:
        raise RuntimeError("Frozen files or scientific source changed")
    complete = (
        all(r["status"] == "complete" for r in statuses.values())
        and evaluation_error is None
    )
    seal(
        out / "completion.json",
        dict(
            status="complete" if complete else "partial",
            fitting_seconds=seconds,
            new_optimizer_nfev=nfev,
            optimization_forward_calls=calls,
            accounting="exact for completed stages; last durable checkpoint lower bound for interrupted stage",
            incomplete_stages=incomplete,
            cap_nfev=cap,
            combined_nfev=nfev + manifest["earlier_nfev"],
            combined_fitting_seconds=seconds + manifest["earlier_fitting_seconds"],
            precheck_forward_calls=sum(
                r.get("precheck_forward_calls", 0) for r in records
            ),
            postcheck_forward_calls=sum(
                r.get("postcheck_forward_calls", 0) for r in records
            ),
            user_acceptance="pending",
            neural_training=False,
        ),
    )
    print(f"Finished {out}: {'complete' if complete else 'partial'}", flush=True)


if __name__ == "__main__":
    main()
