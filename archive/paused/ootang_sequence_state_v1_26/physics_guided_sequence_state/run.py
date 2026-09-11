"""Bounded same-weight diagnostics with one separately budgeted sealed replay."""

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
import pandas as pd

from physics_guided_forecast_error.artifacts import (
    ROOT, check_hashes, check_index, index_artifacts, load_observations, read_json, seal, sha,
)
from physics_guided_sequence.core import Budget
from .audit import check_tables
from .core import analysis_tables, comparisons, components, effects, probe_case
from .support import CONFIG, CONFIG_SHA, PLAN_SHA, arrays, evaluation, require, source_guard, specification, table


def now():
    return datetime.now(timezone.utc).isoformat()


def case_name(h, sample, seed):
    return f"h{h}_{sample}_s{seed}"


def evaluate(spec, callback=None, state=None):
    source = ROOT / spec["source_run"]
    inputs, cases, records = {}, {}, []
    budget = Budget(spec["limits_per_evaluation"])
    if state is not None:
        state["counts"] = budget.counts
    for h in spec["prefixes"]:
        inputs[h] = evaluation(source, spec, h)
        for sample in spec["sample_sources"]:
            for seed in spec["seeds"]:
                data, record = probe_case(source, spec, h, sample, seed, inputs[h], budget)
                cases[h, sample, seed] = data
                records.append(record)
                if callback:
                    callback(case_name(h, sample, seed), data, record)
    require(budget.counts == spec["limits_per_evaluation"], "Registered evaluation counts differ")
    return cases, inputs, pd.DataFrame(records), dict(budget.counts)


def make_tables(spec, cases, inputs, dates, labels):
    values = list(components(spec, cases, inputs))
    result = analysis_tables(spec, values, dates, labels)
    result["effects"] = effects(spec, values)
    result["comparisons"] = comparisons(result["metrics"], spec["strict_mean_tolerance_mm"])
    audit = check_tables(spec, values, result, dates, labels, ROOT / spec["source_run"])
    return result, audit, values


def plot(out, spec, values, labels):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for sample in spec["sample_sources"]:
        fig, axes = plt.subplots(4, 2, figsize=(12, 13), layout="constrained")
        for col, h in enumerate(spec["scoring_prefixes"]):
            data = next(d for info, d in values if info == dict(fit_days=h, sample_source=sample, component="ensemble"))
            need = labels[h:h+180]-data["base"][h:]
            for point, station in enumerate(spec["point_order"]):
                ax = axes[point, col]
                for series, color, style, name in ((need, "black", "-", "Required correction (observed - B+)"),
                        (data["carry"], "#126a83", "-", "CARRY"), (data["s0"], "#c67e24", "--", "S0, same weights")):
                    ax.plot(np.arange(1, 181), series[:, point], color=color, linestyle=style, label=name)
                for day in range(30, 180, 30):
                    ax.axvline(day+0.5, color="0.8", linewidth=0.6)
                ax.axhline(0, color="0.6", linewidth=0.6)
                ax.set(title=f"{sample} | {station} | origin {h}", xlabel="Forecast day", ylabel="Correction (mm)")
                ax.grid(alpha=0.12)
        handles, texts = axes[0, 0].get_legend_handles_labels()
        fig.legend(handles, texts, loc="outside upper center", ncol=3, title="Fixed e100 weights | conditional historical diagnosis")
        fig.savefig(out / f"correction_{sample}.png", dpi=150)
        plt.close(fig)


def summary(records, counts, audit, protected, sources):
    return dict(passed=True, checkpoints_checked=len(records), counts=counts, **audit,
                max_original_difference_mm=float(records.max_original_difference_mm.max()),
                max_numpy_difference_mm=float(records.max_numpy_difference_mm.max()),
                max_effect_reference_difference_mm=float(records.max_effect_reference_difference_mm.max()),
                protected_files_checked=len(protected), source_files_checked=len(sources),
                neural_updates=0, physical_calls=0, scaler_fits=0, probability_scale_fits=0,
                probability_results_changed=False, raw_observation_as_of_verified="unknown",
                s0_promoted_to_model=False)


def execute(out, state):
    started, spec = time.monotonic(), specification()
    protected = source_guard(spec)
    source = ROOT / spec["source_run"]
    paths = [*sorted((ROOT / "code/physics_guided_sequence_state").glob("*.py")),
             ROOT / "tests/test_physics_guided_sequence_state.py", CONFIG, ROOT / spec["protocol"]]
    sources = {str(p.relative_to(ROOT)): sha(p) for p in paths}
    for name in sources:
        destination = out / "sources" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    shutil.copyfile(source / "input.csv", out / "input.csv")
    seal(out / "protected_before.json", protected)
    seal(out / "manifest.json", dict(specification=spec, config_sha256=CONFIG_SHA, plan_sha256=PLAN_SHA,
         sources=sources, started_utc=now(), input_sha256=sha(out / "input.csv"),
         git_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
         dependencies={p: importlib.metadata.version(p) for p in ("numpy", "pandas", "torch", "matplotlib")}))

    def save_case(name, data, record):
        np.savez_compressed(out / f"{name}.npz", **data)
        print(f"evaluated {name}; parent/NumPy checks passed", flush=True)

    cases, inputs, records, counts = evaluate(spec, save_case, state)
    records.to_csv(out / "cases.csv", index=False)
    seal(out / "prediction_lock.json", dict(locked_utc=now(), files={p.name: sha(p) for p in [*out.glob("h*.npz"), out / "cases.csv"]}))
    scoring_time = now()
    dates, _, labels = load_observations(out / "input.csv", 792)
    tables, audit, values = make_tables(spec, cases, inputs, dates, labels)
    for name, frame in tables.items():
        frame.to_csv(out / f"{name}.csv", index=False)
        pd.testing.assert_frame_equal(table(out / f"{name}.csv"), frame, check_exact=True)
    seal(out / "scoring.json", dict(label_read_utc=scoring_time, prediction_lock_sha256=sha(out / "prediction_lock.json")))
    plot(out, spec, values, labels)
    result = summary(records, counts, audit, protected, sources)
    seal(out / "verification.json", result)
    check_hashes(protected)
    check_hashes(sources)
    seal(out / "completed.json", dict(execution_complete=True, numerical_verification=True,
                                     elapsed_seconds=time.monotonic()-started, completed_utc=now()))
    return result


def check_time_lock(out, spec):
    lock = read_json(out / "prediction_lock.json")
    expected = {case_name(h, sample, seed)+".npz" for h in spec["prefixes"]
                for sample in spec["sample_sources"] for seed in spec["seeds"]} | {"cases.csv"}
    require(set(lock["files"]) == expected, "Incomplete fixed-output lock")
    check_hashes(lock["files"], out)
    scoring = read_json(out / "scoring.json")
    require(scoring["prediction_lock_sha256"] == sha(out / "prediction_lock.json")
            and datetime.fromisoformat(lock["locked_utc"]) <= datetime.fromisoformat(scoring["label_read_utc"]),
            "Observation scoring preceded the fixed-output lock")


def verify(out):
    spec = specification()
    check_index(out)
    complete = read_json(out / "completed.json")
    require(complete["execution_complete"] and complete["numerical_verification"]
            and 0 < complete["elapsed_seconds"] <= spec["hard_timeout_seconds"]
            and read_json(out / "launcher.json")["exitcode"] == 0, "Diagnostic execution was not successful")
    manifest = read_json(out / "manifest.json")
    require(manifest["specification"] == spec and manifest["config_sha256"] == CONFIG_SHA
            and manifest["plan_sha256"] == PLAN_SHA, "Changed diagnostic specification")
    protected = source_guard(spec)
    require(protected == read_json(out / "protected_before.json"), "Changed source protection inventory")
    check_hashes(manifest["sources"])
    check_hashes(manifest["sources"], out / "sources")
    require(sha(out / "input.csv") == manifest["input_sha256"] == sha(ROOT / spec["source_run"] / "input.csv"), "Changed observations")
    check_time_lock(out, spec)
    cases, inputs, records, counts = evaluate(spec)
    replay_count, replay_maximum = 0, 0.0
    for (h, sample, seed), data in cases.items():
        saved = arrays(out / f"{case_name(h, sample, seed)}.npz")
        require(set(saved) == set(data), "Changed diagnostic array keys")
        for key, value in data.items():
            # Same implementation is deterministic; independent NumPy has its own tolerance.
            np.testing.assert_array_equal(saved[key], value)
            replay_count += value.size
            replay_maximum = max(replay_maximum, float(np.nanmax(np.abs(saved[key]-value))))
    pd.testing.assert_frame_equal(table(out / "cases.csv"), records, check_exact=True)
    dates, _, labels = load_observations(out / "input.csv", 792)
    tables, audit, _ = make_tables(spec, cases, inputs, dates, labels)
    for name, frame in tables.items():
        pd.testing.assert_frame_equal(table(out / f"{name}.csv"), frame, check_exact=True)
    result = summary(records, counts, audit, protected, manifest["sources"])
    require(result == read_json(out / "verification.json"), "Original numerical verification record differs")
    check_hashes(protected)
    check_hashes(manifest["sources"])
    return dict(**result, replayed_array_values=replay_count, max_replay_difference_mm=replay_maximum)


def worker(out, mode, sender):
    state = dict(counts={})
    if mode == "verify":
        try:
            sender.send(verify(out))
        finally:
            sender.close()
        return
    with (out / "run.log").open("x", buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                sender.send(execute(out, state))
            except BaseException as error:
                traceback.print_exc()
                seal(out / "failed.json", dict(error=repr(error), failed_utc=now(), **state))
                raise
            finally:
                sender.close()


def launch(out, mode, spec):
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=worker, args=(out, mode, sender))
    started = time.monotonic()
    process.start()
    sender.close()
    while process.is_alive() and time.monotonic()-started < spec["hard_timeout_seconds"]:
        process.join(timeout=min(1, max(0, spec["hard_timeout_seconds"]-(time.monotonic()-started))))
    if process.is_alive():
        print("Registered 120-second hard timeout reached; terminating diagnostic worker.", flush=True)
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
    else:
        process.join()
    result = receiver.recv() if process.exitcode == 0 and receiver.poll() else None
    receiver.close()
    exitcode = process.exitcode if process.exitcode else 0 if result is not None else 1
    return result, exitcode, time.monotonic()-started


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.run_id), "Unsafe run id")
    spec = specification()
    out = ROOT / "results/ootang_bplus_v1_26" / args.run_id
    if not args.verify:
        out.mkdir(parents=True, exist_ok=False)
    result, exitcode, elapsed = launch(out, "verify" if args.verify else "run", spec)
    if not args.verify:
        if exitcode != 0 and not (out / "failed.json").exists():
            seal(out / "failed.json", dict(error=f"Worker stopped with code {exitcode}", failed_utc=now()))
        seal(out / "launcher.json", dict(exitcode=exitcode, wall_seconds=elapsed))
        index_artifacts(out)
    print(json.dumps(result if args.verify and result is not None else dict(run_directory=str(out), exitcode=exitcode), indent=2))
    raise SystemExit(exitcode)


if __name__ == "__main__":
    main()
