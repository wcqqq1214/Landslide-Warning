"""Execute the fixed nine-fit auxiliary comparison, with chronological locks."""

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
    array_sha,
    check_hashes,
    index_artifacts,
    seal,
    sha,
)
from physics_guided_rate_diagnostics.workflow import no_model_runtime
from .core import Budget, constant_fit, correction, ridge_fit, standardize
from .workflow import (
    CONFIG,
    CONFIG_SHA,
    NAMES,
    PLAN_SHA,
    copy_input,
    load_case,
    metric_tables,
    now,
    plot,
    read_labels,
    source_guard,
    specification,
)


def execute(out, budget):
    spec, started = specification(), time.monotonic()
    protected = source_guard(spec)
    paths = [
        *sorted((ROOT / "code/physics_guided_temporal_features").glob("*.py")),
        ROOT / "tests/test_physics_guided_temporal_features.py",
        CONFIG,
        ROOT / spec["protocol"],
    ]
    sources = {str(p.relative_to(ROOT)): sha(p) for p in paths}
    for name in sources:
        target = out / "sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    copy_input(spec, out / "input_prefix_612.csv")
    seal(out / "protected_before.json", protected)
    seal(
        out / "manifest.json",
        dict(
            specification=spec,
            config_sha256=CONFIG_SHA,
            plan_sha256=PLAN_SHA,
            sources=sources,
            input_sha256=sha(out / "input_prefix_612.csv"),
            feature_names=NAMES,
            git_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            started_utc=now(),
            numpy_version=np.__version__,
        ),
    )
    previous = None
    for origin in spec["origins"]:
        seal(
            out / f"label_access_{origin}.json",
            dict(prefix=origin, opened_utc=now(), preceding_lock=previous),
        )
        labels = read_labels(out / "input_prefix_612.csv", origin)
        case, record = load_case(spec, origin)
        if array_sha(labels) != record["training_label_sha256"]:
            raise ValueError("Teacher and auxiliary training labels differ")
        np.savez_compressed(out / f"case_{origin}.npz", **case)
        seal(out / f"teacher_{origin}.json", record)
        scaler = standardize(case["raw_features"][:origin].copy(), origin, budget)
        np.savez_compressed(out / f"scaler_{origin}.npz", **scaler)
        x = (case["raw_features"] - scaler["mean"]) / scaler["std"]
        target = labels[30:] - case["base_mean"][30:origin]
        offset = constant_fit(target, origin - 30, budget)
        np.savez_compressed(
            out / f"constant_{origin}.npz",
            offset=offset,
            prediction=case["base_mean"] + offset,
        )
        for name, dimensions in spec["representations"].items():
            coefficient = ridge_fit(
                x[30:origin, :dimensions].copy(),
                target.copy(),
                origin - 30,
                spec["ridge_lambda"],
                spec["target_scale_mm"],
                budget,
            )
            applied = correction(
                x[:, :dimensions], coefficient, spec["target_scale_mm"]
            )
            data_objective = float(
                np.sum(((applied[30:origin] - target) / spec["target_scale_mm"]) ** 2)
                / (origin - 30)
            )
            penalty = float(spec["ridge_lambda"] * np.sum(coefficient[1:] ** 2))
            np.savez_compressed(
                out / f"model_{origin}_{name}.npz",
                coefficient=coefficient,
                correction=applied,
                prediction=case["base_mean"] + applied,
                data_objective=data_objective,
                penalty=penalty,
                total_objective=data_objective + penalty,
            )
        names = [
            f"case_{origin}.npz",
            f"teacher_{origin}.json",
            f"scaler_{origin}.npz",
            f"constant_{origin}.npz",
            *[f"model_{origin}_{name}.npz" for name in spec["representations"]],
        ]
        previous = f"prefix_{origin}_locked.json"
        seal(
            out / previous,
            dict(
                origin=origin,
                locked_utc=now(),
                training_label_sha256=array_sha(labels),
                outputs={name: sha(out / name) for name in names},
                counts=dict(budget.counts),
            ),
        )
        print(
            json.dumps(dict(origin=origin, status="outputs_locked", **budget.counts)),
            flush=True,
        )
    seal(
        out / "label_access_612.json",
        dict(prefix=612, opened_utc=now(), preceding_lock=previous),
    )
    labels = read_labels(out / "input_prefix_612.csv", 612)
    metrics, comparison = metric_tables(out, spec, labels)
    metrics.to_csv(out / "metrics.csv", index=False, lineterminator="\n")
    comparison.to_csv(out / "comparisons.csv", index=False, lineterminator="\n")
    plot(out, spec, labels)
    seal(
        out / "execution.json",
        dict(
            **budget.counts, **{k: v for k, v in spec.items() if k.startswith("new_")}
        ),
    )
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
            elapsed_seconds=time.monotonic() - started,
            completed_utc=now(),
            **budget.counts,
        ),
    )


def worker(out):
    budget = Budget(specification())
    with (out / "run.log").open("x", buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                execute(out, budget)
            except BaseException as error:
                traceback.print_exc()
                seal(
                    out / "failed.json",
                    dict(error=repr(error), failed_utc=now(), **budget.counts),
                )
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.run_id):
        raise ValueError("Unsafe run id")
    out = ROOT / "results/ootang_bplus_v1_14" / args.run_id
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    process = multiprocessing.get_context("spawn").Process(target=worker, args=(out,))
    process.start()
    process.join(specification()["timeout_seconds"])
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
