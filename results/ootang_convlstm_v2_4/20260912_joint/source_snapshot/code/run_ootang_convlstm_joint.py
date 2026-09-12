"""One authorized paired run. Existing output means no retry."""

import copy
import csv
from datetime import datetime, timezone
import json
import platform
import signal
import subprocess
import sys
import time
import traceback

import numpy as np
import torch
import pandas as pd
import matplotlib

from physics_guided.training import optimizer
from physics_guided_forecast_error.artifacts import load_observations
from physics_guided_origin_learning.support import read_teachers
from physics_guided_joint import (
    ROOT, CONFIG, ARMS, check_hashes, sha, write_json, specification,
    training_inputs, evaluation_inputs, new_models, joint_step, objectives,
    predict_distribution, score_saved, decide, reference_metric_error,
)

IMPLEMENTATION = (
    "code/physics_guided_joint.py", "code/run_ootang_convlstm_joint.py",
    "scripts/verify_ootang_convlstm_joint.py", "tests/test_physics_guided_joint.py",
    "config/ootang_convlstm_joint.v2_4.json",
)


def append_csv(path, record):
    exists = path.exists()
    with path.open("a", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(record))
        if not exists:
            writer.writeheader()
        writer.writerow(record)


class Run:
    def __init__(self, spec):
        self.spec = spec
        self.out = ROOT / spec["output_dir"]
        self.started = time.monotonic()
        self.started_utc = datetime.now(timezone.utc).isoformat()
        self.deadline = datetime.fromisoformat(spec["work_deadline_utc"]).timestamp()
        self.counts = dict(mean_updates=0, scale_updates=0, coordinated_iterations=0)
        self.events = []

    def check(self):
        if time.time() >= self.deadline or time.monotonic() - self.started >= self.spec["run_limit_seconds"]:
            raise TimeoutError("Frozen work or single-run deadline reached; no retry")

    def step(self, branch):
        self.check()
        key = branch + "_updates"
        if self.counts[key] >= self.spec["max_" + key]:
            raise RuntimeError("Frozen branch update cap reached")
        if self.counts["mean_updates"] + self.counts["scale_updates"] >= self.spec["max_optimizer_steps"]:
            raise RuntimeError("Frozen total optimizer cap reached")
        self.counts[key] += 1

    def event(self, kind, **fields):
        value = dict(sequence=len(self.events), utc=datetime.now(timezone.utc).isoformat(),
                     kind=kind, **fields)
        self.events.append(value)
        with (self.out / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(value) + "\n")

    def labels(self, h):
        self.check()
        if h != (432, 612, 792)[sum(e["kind"] == "observation_prefix_read" for e in self.events)]:
            raise RuntimeError("Unregistered label access")
        if h > 432 and not any(e["kind"] == "prefix_locked" and e["prefix"] == h - 180 for e in self.events):
            raise RuntimeError("All earlier arm/seed predictions must be locked first")
        self.event("observation_prefix_read", rows=h)
        return load_observations(ROOT / self.spec["data"], h)[2]

    def fit(self, h):
        spec, out = self.spec, self.out
        labels = self.labels(h)
        pool = read_teachers(ROOT / spec["data"], ROOT / spec["physical_source"], h)
        table, batch, context, sigma0 = training_inputs(spec, h, labels, pool)
        eval_table, eval_batch, eval_context = evaluation_inputs(spec, h, labels, pool)
        paths = []
        for name, frame in ((f"queries_{h}.csv", table), (f"evaluation_queries_{h}.csv", eval_table)):
            frame.to_csv(out / name, index=False)
            paths.append(out / name)
        for name, data in ((f"training_inputs_{h}.npz", batch.payload()),
                           (f"evaluation_inputs_{h}.npz", eval_batch.payload())):
            np.savez_compressed(out / name, **data)
            paths.append(out / name)
        init_path = out / f"initial_scale_{h}.json"
        write_json(init_path, dict(sigma_mm=sigma0.tolist(),
                   oof_rows=int(sum(table.block == 1)),
                   unique_oof_targets=int(table.loc[table.block == 1, "target"].nunique())))
        paths.append(init_path)
        predictions = {a: dict(means=[], sigmas=[]) for a in ARMS}
        all_objectives = []
        for seed in spec["seeds"]:
            initial_mean, initial_scale = new_models(seed, sigma0)
            if sum(p.numel() for p in initial_mean.parameters()) != spec["mean_parameters"]:
                raise ValueError("Frozen mean architecture changed")
            if sum(p.numel() for p in initial_scale.parameters()) != spec["scale_parameters"]:
                raise ValueError("Frozen scale architecture changed")
            initial = dict(mean=copy.deepcopy(initial_mean.state_dict()),
                           scale=copy.deepcopy(initial_scale.state_dict()))
            init_path = out / f"initial_{h}_seed{seed}.pt"
            torch.save(initial, init_path)
            paths.append(init_path)
            for arm in ARMS:
                mean, scale = copy.deepcopy(initial_mean), copy.deepcopy(initial_scale)
                for branch, model in (("mean", mean), ("scale", scale)):
                    for key, tensor in model.state_dict().items():
                        if not torch.equal(tensor, initial[branch][key]):
                            raise AssertionError("Paired initialization differs")
                with torch.no_grad():
                    if torch.count_nonzero(mean(batch.encoder, batch.decoder)).item():
                        raise AssertionError("Initial correction must be exactly zero")
                    np.testing.assert_allclose(
                        scale(context.features(context.baseline)).numpy(),
                        np.broadcast_to(sigma0, context.baseline.shape), rtol=0, atol=1e-10,
                    )
                before = objectives(mean, scale, batch, context, arm == "DETACHED")
                mean_op, scale_op = optimizer(mean), optimizer(scale)
                for epoch in range(1, spec["epochs"] + 1):
                    self.check()
                    record = joint_step(mean, scale, mean_op, scale_op, batch, context,
                                        arm == "DETACHED", self.step)
                    self.counts["coordinated_iterations"] += 1
                    append_csv(out / "training.csv", dict(prefix=h, seed=seed, arm=arm, epoch=epoch, **record))
                after = objectives(mean, scale, batch, context, arm == "DETACHED")
                all_objectives.append(dict(prefix=h, seed=seed, arm=arm, initial=before, final=after))
                checkpoint = out / f"model_{h}_{arm}_seed{seed}_e100.pt"
                torch.save(dict(mean=mean.state_dict(), scale=scale.state_dict()), checkpoint)
                paths.append(checkpoint)
                mu, sigma = predict_distribution(mean, scale, eval_batch, eval_context, h)
                predictions[arm]["means"].append(mu)
                predictions[arm]["sigmas"].append(sigma)
                print(f"prefix={h} arm={arm} seed={seed} e100 complete; elapsed={time.monotonic() - self.started:.2f}s", flush=True)
        for arm in ARMS:
            path = out / f"prediction_{h}_{arm}.npz"
            np.savez_compressed(path, **{k: np.stack(v) for k, v in predictions[arm].items()})
            paths.append(path)
        objective_path = out / f"objectives_{h}.json"
        write_json(objective_path, all_objectives)
        paths.append(objective_path)
        hashes = {p.name: sha(p) for p in paths}
        write_json(out / f"prefix_lock_{h}.json", dict(prefix=h, artifacts=hashes))
        self.event("prefix_locked", prefix=h, artifacts=hashes)


def figures(out, daily):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    for h in (432, 612):
        fig, axes = plt.subplots(2, 2, figsize=(12, 7), sharex=True)
        for ax, station in zip(axes.flat, ("ATU1", "ATU5", "MJ3", "MJ1")):
            subset = daily[(daily.outer_days == h) & (daily.station == station)]
            joint = subset[subset.strategy == "JOINT"]
            dates = pd.to_datetime(joint.date)
            ax.plot(dates, joint.observed_mm, color="black", lw=1.1, label="observed")
            ax.fill_between(dates, joint.lower_90_mm, joint.upper_90_mm, color="#2686b1", alpha=.18, label="JOINT 90%")
            for arm, color, style in (("P0", "#db8436", "-"), ("JOINT", "#147caa", "-"),
                                      ("DETACHED", "#8658a2", "--")):
                frame = subset[subset.strategy == arm]
                ax.plot(pd.to_datetime(frame.date), frame.mean_mm, color=color, ls=style, lw=1, label=arm)
            ax.axvline(pd.Timestamp("2016-07-01") + pd.Timedelta(days=h), color="#888888", ls=":", lw=1)
            ax.set_title(station)
            ax.set_ylabel("Displacement / mm")
            ax.grid(alpha=.15)
            ax.tick_params(axis="x", rotation=20)
        handles, labels = axes[0, 0].get_legend_handles_labels()
        fig.legend(handles, labels, loc="lower center", ncol=5)
        fig.suptitle(f"Frozen {h}-day prefix + 180-day prediction | vertical line: forecast origin")
        fig.tight_layout(rect=(0, .05, 1, .96))
        fig.savefig(out / f"curves_{h}.png", dpi=170)
        fig.savefig(out / f"curves_{h}.pdf")
        plt.close(fig)


def main():
    if len(sys.argv) != 1:
        raise ValueError("No run overrides are permitted")
    spec = specification()
    if time.time() >= datetime.fromisoformat(spec["implementation_deadline_utc"]).timestamp():
        raise TimeoutError("Implementation allocation exhausted; formal run not authorized after this boundary")
    for path in IMPLEMENTATION:
        committed = subprocess.check_output(["git", "show", "HEAD:" + path], cwd=ROOT)
        if committed != (ROOT / path).read_bytes():
            raise ValueError(f"Implementation must be committed before the single run: {path}")
    run = Run(spec)
    run.out.mkdir(parents=True, exist_ok=False)
    (run.out / "config.json").write_bytes(CONFIG.read_bytes())
    sources = {**spec["source_sha256"], **{p: sha(ROOT / p) for p in IMPLEMENTATION}}
    write_json(run.out / "manifest.json", dict(
        git_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        config_sha256=sha(CONFIG), sources=sources, config=spec,
        environment=dict(python=sys.version, torch=torch.__version__, numpy=np.__version__,
                         pandas=pd.__version__, matplotlib=matplotlib.__version__,
                         platform=platform.platform(), cpu_threads=1, dtype="float64"),
        single_start=True, held_out_window="none; exposed historical windows",
    ))
    for path in IMPLEMENTATION:
        target = run.out / "source_snapshot" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / path).read_bytes())
    status, error = "failed", None
    def deadline_handler(signum, frame):
        raise TimeoutError("Single-run wall time reached")
    signal.signal(signal.SIGALRM, deadline_handler)
    signal.setitimer(signal.ITIMER_REAL, min(spec["run_limit_seconds"], run.deadline - time.time()))
    try:
        run.event("run_started")
        for h in spec["outer_days"]:
            run.fit(h)
        labels = run.labels(792)
        dates = pd.date_range("2016-07-01", periods=792)
        tables = score_saved(run.out, spec, labels, dates)
        for name, frame in tables.items():
            frame.to_csv(run.out / name, index=False)
        reference_error = reference_metric_error(tables["metrics.csv"], spec)
        write_json(run.out / "reference_checks.json", dict(max_metric_error_mm=reference_error))
        write_json(run.out / "decision.json", decide(tables["metrics.csv"], spec))
        figures(run.out, tables["daily_predictions.csv"])
        check_hashes(sources)
        if run.counts != dict(mean_updates=1200, scale_updates=1200, coordinated_iterations=1200):
            raise AssertionError("The fixed experiment was not completed")
        run.check()
        status = "completed"
        run.event("run_completed")
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        (run.out / "failure.txt").write_text(traceback.format_exc())
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        execution = dict(status=status, error=error, started_utc=run.started_utc,
                         ended_utc=datetime.now(timezone.utc).isoformat(),
                         elapsed_seconds=time.monotonic() - run.started, **run.counts,
                         optimizer_steps=run.counts["mean_updates"] + run.counts["scale_updates"],
                         physical_forward_calls=0, scaler_fits=0, retry_count=0)
        write_json(run.out / "execution.json", execution)
        write_json(run.out / "artifact_manifest.json", {
            str(p.relative_to(run.out)): sha(p)
            for p in sorted(run.out.rglob("*")) if p.is_file()
        })
        print(json.dumps(execution), flush=True)


if __name__ == "__main__":
    main()
