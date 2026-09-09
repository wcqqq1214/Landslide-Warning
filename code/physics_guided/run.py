"""Independent v1.1 command; never invokes the historic warning pipeline.

PYTHONPATH=code .venv/bin/python -m physics_guided.run --run-id <new-id> --phase all
"""

import argparse
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
import traceback
import numpy as np
import pandas as pd
from .reference import ROOT, prepare, load, frozen_theta, save_json, sha
from .data import read_data, COUNTS
from .preflight import reproduce
from .calibration import calibrate
from .mechanics import Mechanics
from .features import fit_scalers
from .protocol import specification
from .validation import (
    validate_physics,
    validate_probability,
    validate_switch,
    validate_dates,
)
from .training import train_route, rank_routes, setup
from .reporting import export_stage, acceptance, plot_results


def source_hashes():
    return {
        str(p.relative_to(ROOT)): sha(p)
        for p in sorted((ROOT / "code/physics_guided").glob("*"))
        if p.suffix in (".py", ".c")
    }


def register_run(out, phase):
    config = ROOT / "config/ootang_bplus_probabilistic.v1_1.json"
    if json.loads(config.read_text()) != specification():
        raise ValueError(
            "Configuration differs from implemented v1.1; register a new version"
        )
    data = dict(
        command=sys.argv,
        phase=phase,
        started_utc=pd.Timestamp.now(tz="UTC").isoformat(),
        python=sys.version,
        platform=platform.platform(),
        pid=os.getpid(),
        git_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        sources=source_hashes(),
        config_sha256=sha(config),
        plan_sha256=sha(ROOT / specification()["plan"]),
        data_sha256=sha(ROOT / "data/monitoring_data.csv"),
        dependencies={
            k: importlib.metadata.version(k)
            for k in ["numpy", "scipy", "torch", "pandas", "matplotlib"]
        },
        deterministic=True,
        device="cpu",
        dtype="float64",
        historical_backtest=True,
        user_acceptance="pending",
    )
    save_json(out / f"manifest_{phase}_{time.time_ns()}.json", data)
    return data


def existing_prediction(path, current_sources):
    record = json.loads((path / "execution.json").read_text())
    if record["sources"] != current_sources:
        raise ValueError(f"Cannot reuse predictions after source changes: {path}")
    values = np.load(path / "selected_predictions.npz")
    return (
        values["means"],
        values["sigmas"],
        json.loads((path / "selection.json").read_text()),
    )


def numerical_gate(ref, theta, drivers, labels, out, name):
    path = out / f"gate_{name}.json"
    identity = dict(sources=source_hashes(), theta=np.asarray(theta).tolist())
    if path.exists():
        record = json.loads(path.read_text())
        if record.get("identity") == identity:
            return record["status"] == "passed"
    try:
        validate_physics(ref, theta, drivers, labels[:792], out, name)
        save_json(
            out / f"validation_switch_{name}.json",
            validate_switch(Mechanics(ref, theta, drivers.prefix(120))),
        )
        record = dict(status="passed", identity=identity)
    except Exception as exc:
        record = dict(
            status="failed",
            identity=identity,
            failure=repr(exc),
            traceback=traceback.format_exc(),
        )
        print(
            f"M2 {name} numerical gate failed; M1 remains independent: {exc}",
            flush=True,
        )
    save_json(path, record)
    return record["status"] == "passed"


def run(args):
    os.chdir(ROOT)
    setup(0)
    if not args.run_id or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for c in args.run_id
    ):
        raise ValueError("run_id must be a simple unique directory name")
    out = ROOT / "results/ootang_bplus_v1_1" / args.run_id
    out.mkdir(parents=True, exist_ok=True)
    manifest = register_run(out, args.phase)
    refdir = ROOT / "runtime/ootang_bplus_v1_1/reference"
    ref = (
        prepare(refdir)
        if args.phase in ("all", "prepare") or not refdir.exists()
        else load(refdir)
    )
    drivers, labels = read_data(ROOT / "data/monitoring_data.csv")
    theta_final, _ = frozen_theta(ref)
    if args.phase in ("all", "prepare"):
        reproduce(ref, drivers, labels, out)
        save_json(
            out / "reference_provenance.json",
            json.loads((refdir / "provenance.json").read_text()),
        )
        save_json(
            out / "common_validation.json",
            dict(probability=validate_probability(), dates=validate_dates(drivers)),
        )
    # Reproduction is an unconditional dependency gate, not just a prior JSON flag.
    if args.phase not in ("all", "prepare"):
        reproduce(ref, drivers, labels, out)
    if args.phase in ("all", "calibrate"):
        if (out / "prefix_calibrated.json").exists():
            raise FileExistsError(
                "Prefix result already exists; use develop/validate/final or a new run_id"
            )
        calibrate(ref, drivers.prefix(792), labels[:792].copy(), out)
    if args.phase in ("all", "validate"):
        numerical_gate(ref, theta_final, drivers, labels, out, "frozen")
        if (out / "prefix_calibrated.json").exists():
            th = np.array(
                json.loads((out / "prefix_calibrated.json").read_text())["theta"]
            )
            numerical_gate(ref, th, drivers, labels, out, "prefix")
    stages = []
    if args.phase in ("all", "develop"):
        stages.append("development")
    if args.phase in ("all", "final"):
        stages.append("final")
    for stage in stages:
        nfit, end = COUNTS[stage]
        prefix_record = json.loads((out / "prefix_calibrated.json").read_text())
        theta = (
            np.array(prefix_record["theta"]) if stage == "development" else theta_final
        )
        # The selected prefix physics must pass the full-history numerical gate before M2 training.
        m2_valid = numerical_gate(
            ref,
            theta,
            drivers,
            labels,
            out,
            "prefix" if stage == "development" else "frozen",
        )
        if stage == "final":
            global_lock = json.loads((out / "selection.json").read_text())
            if (
                global_lock["sources"] != manifest["sources"]
                or global_lock["config_sha256"] != manifest["config_sha256"]
            ):
                raise ValueError(
                    "Implementation/config changed after development lock; affected development must be recomputed"
                )
        full, train = drivers.prefix(end), drivers.prefix(nfit)
        full_mechanics, train_mechanics = (
            Mechanics(ref, theta, full),
            Mechanics(ref, theta, train),
        )
        scalers = fit_scalers(full, full_mechanics, nfit)
        stage_dir = out / stage
        stage_dir.mkdir(exist_ok=True)
        save_json(
            stage_dir / "scalers.json",
            {"fit_days": nfit, "m1": scalers[0].record(), "m2": scalers[1].record()},
        )
        selections = {}
        for route in ("M1", "M2"):
            path = stage_dir / route
            if route == "M2" and not m2_valid:
                selections[route] = dict(
                    status="failed", reason="Full-history numerical gate failed"
                )
                continue
            if (path / "selection.json").exists():
                _, _, selected = existing_prediction(path, manifest["sources"])
                selections[route] = selected
                continue
            if path.exists() and any(path.glob("*.pt")):
                selections[route] = dict(
                    status="failed",
                    reason="Incomplete prior attempt preserved; use a new run_id for a documented rerun",
                )
                continue
            lock = None
            if stage == "final":
                lock_path = out / "development" / route / "selection.json"
                if not lock_path.exists():
                    selections[route] = dict(
                        status="failed", reason="Development route incomplete"
                    )
                    continue
                lock = json.loads(lock_path.read_text())
                if not (out / "selection.json").exists():
                    raise ValueError(
                        "Final retraining requires prior development ranking lock"
                    )
            path.mkdir(parents=True, exist_ok=True)
            save_json(
                path / "execution.json",
                dict(
                    sources=manifest["sources"],
                    config_sha256=manifest["config_sha256"],
                    physics_theta=theta.tolist(),
                    started_utc=pd.Timestamp.now(tz="UTC").isoformat(),
                ),
            )
            try:
                _, _, selected = train_route(
                    route,
                    stage,
                    train,
                    train_mechanics,
                    full,
                    full_mechanics,
                    scalers,
                    labels[:nfit].copy(),
                    labels[792:1168].copy() if stage == "development" else None,
                    path,
                    locked=lock,
                )
                selections[route] = selected
            except Exception as exc:
                selections[route] = dict(
                    status="failed", failure=repr(exc), traceback=traceback.format_exc()
                )
                save_json(path / "failure.json", selections[route])
                print(
                    f"{stage} {route} failed; independent route continues: {exc}",
                    flush=True,
                )
        if stage == "development":
            baseline = np.sqrt(
                np.mean(
                    (full_mechanics.u[792:] + drivers.y0 - labels[792:1168]) ** 2,
                    axis=0,
                )
            ).mean()
            selection = dict(
                routes=selections,
                ranking=rank_routes(selections, float(baseline)),
                sources=manifest["sources"],
                config_sha256=manifest["config_sha256"],
                frozen_before_final=True,
            )
            save_json(out / "selection.json", selection)
        save_json(stage_dir / "status.json", selections)
    if args.phase in ("all", "report", "develop", "final"):
        report(ref, drivers, labels, out)
    save_json(
        out / f"completion_{args.phase}.json",
        dict(
            status="execution_finished",
            seconds=time.monotonic() - args.started,
            user_acceptance="pending",
        ),
    )


def report(ref, drivers, labels, out):
    for stage, (_, end) in COUNTS.items():
        if stage == "development":
            file = out / "prefix_calibrated.json"
            if not file.exists():
                continue
            theta = np.array(json.loads(file.read_text())["theta"])
        else:
            theta, _ = frozen_theta(ref)
            if not (out / "final").exists():
                continue
        full = drivers.prefix(end)
        baseline = ref.forward(theta, ref.Context(full.forcing)) + drivers.y0
        models = {}
        for route in ("M1", "M2"):
            path = out / stage / route / "selected_predictions.npz"
            if path.exists():
                p = np.load(path)
                models[route] = (p["means"], p["sigmas"])
        status_file = out / stage / "status.json"
        statuses = json.loads(status_file.read_text()) if status_file.exists() else {}
        failed = [
            route
            for route, record in statuses.items()
            if record.get("status") == "failed"
        ]
        predictions, metrics = export_stage(
            stage, full, baseline, models, labels[:end], out, failed_routes=failed
        )
        if stage == "final":
            save_json(out / "acceptance.json", acceptance(metrics))
            plot_results(predictions, labels, drivers, out / "figures")
    for kind in ("predictions", "metrics"):
        frames = [
            pd.read_csv(out / f"{kind}_{stage}.csv")
            for stage in COUNTS
            if (out / f"{kind}_{stage}.csv").exists()
        ]
        if frames:
            pd.concat(frames, ignore_index=True).to_csv(
                out / f"{kind}.csv", index=False
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--phase",
        choices=[
            "all",
            "prepare",
            "calibrate",
            "validate",
            "develop",
            "final",
            "report",
        ],
        default="all",
    )
    args = parser.parse_args()
    args.started = time.monotonic()
    run(args)


if __name__ == "__main__":
    main()
